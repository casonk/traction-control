package broker

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/actions/scaleset"
	"github.com/actions/scaleset/listener"
)

// JitClient is the part of the scaleset client the scaler uses.
type JitClient interface {
	GenerateJitRunnerConfig(ctx context.Context, setting *scaleset.RunnerScaleSetJitRunnerSetting, scaleSetID int) (*scaleset.RunnerScaleSetJitRunnerConfig, error)
	RemoveRunner(ctx context.Context, runnerID int64) error
}

// Scaler serves one repository's scale set: one VM per job, deleted when the job completes.
type Scaler struct {
	Repo          string
	ScaleSetID    int
	Jit           JitClient
	Cloud         Cloud
	Fleet         *Fleet
	Log           *slog.Logger
	DryRun        bool
	Now           func() time.Time
	NewName       func() string
	BaseCtx       context.Context
	CreateTimeout time.Duration
	DeleteRetries int
	RetryWait     time.Duration

	wg sync.WaitGroup
}

var _ listener.Scaler = (*Scaler)(nil)

// RandomName returns a fresh VM name: the broker prefix plus eight hex characters (11 characters in all).
func RandomName() string {
	b := make([]byte, 4)
	if _, err := rand.Read(b); err != nil {
		panic(err) // the system random source failing is not recoverable
	}
	return VMPrefix + hex.EncodeToString(b)
}

func (s *Scaler) defaults() {
	if s.Now == nil {
		s.Now = time.Now
	}
	if s.NewName == nil {
		s.NewName = RandomName
	}
	if s.BaseCtx == nil {
		s.BaseCtx = context.Background()
	}
	if s.CreateTimeout == 0 {
		s.CreateTimeout = 10 * time.Minute
	}
	if s.DeleteRetries == 0 {
		s.DeleteRetries = 4
	}
	if s.RetryWait == 0 {
		s.RetryWait = 15 * time.Second
	}
	if s.Log == nil {
		s.Log = slog.New(slog.DiscardHandler)
	}
}

// Adopt takes over VMs of this repository that already exist, after a restart, so their jobs can still clean up.
func (s *Scaler) Adopt(vms []VM) {
	s.defaults()
	for _, vm := range vms {
		if vm.Repo == s.Repo {
			s.Fleet.Adopt(vm.Name, vm.Repo, vm.Created)
			s.Log.Info("adopted an existing VM", "vm", vm.Name)
		}
	}
}

// HandleDesiredRunnerCount creates VMs until this repository has as many as jobs want one.
func (s *Scaler) HandleDesiredRunnerCount(_ context.Context, count int) (int, error) {
	s.defaults()
	current := s.Fleet.CountRepo(s.Repo)
	if count <= current {
		return current, nil
	}
	for i := current; i < count; i++ {
		name := s.NewName()
		if s.DryRun {
			s.Log.Info("dry run: would create a VM", "vm", name, "repo", s.Repo)
			continue
		}
		if !s.Fleet.Reserve(name, s.Repo, s.Now()) {
			s.Log.Warn("not creating a VM: the cap is reached", "max", s.Fleet.Max(), "repo", s.Repo)
			break
		}
		s.wg.Add(1)
		go s.create(name)
	}
	return s.Fleet.CountRepo(s.Repo), nil
}

func (s *Scaler) create(name string) {
	defer s.wg.Done()
	ctx, cancel := context.WithTimeout(s.BaseCtx, s.CreateTimeout)
	defer cancel()
	log := s.Log.With("vm", name, "repo", s.Repo)

	jit, err := s.Jit.GenerateJitRunnerConfig(ctx, &scaleset.RunnerScaleSetJitRunnerSetting{Name: name}, s.ScaleSetID)
	if err != nil {
		log.Error("could not get a runner configuration", "error", err)
		s.Fleet.Release(name)
		return
	}
	runnerID := 0
	if jit.Runner != nil {
		runnerID = jit.Runner.ID
	}
	s.Fleet.SetState(name, StateCreating, runnerID)

	spec := VMSpec{Name: name, Repo: s.Repo, Created: s.Now(), JITConfig: jit.EncodedJITConfig}
	if err := s.Cloud.CreateVM(ctx, spec); err != nil {
		log.Error("could not create the VM", "error", err)
		s.destroy(name, runnerID) // partial resources, and the runner registration that will never connect
		return
	}
	if s.Fleet.Transition(name, StateCreating, StateIdle) {
		log.Info("VM created")
	} else {
		log.Info("VM created; its job already started")
	}
}

// HandleJobStarted marks the VM busy.
func (s *Scaler) HandleJobStarted(_ context.Context, job *scaleset.JobStarted) error {
	s.defaults()
	if !s.Fleet.SetState(job.RunnerName, StateBusy, 0) {
		s.Log.Warn("a job started on a runner this broker does not track", "runner", job.RunnerName)
	}
	return nil
}

// HandleJobCompleted deletes the job's VM, whatever the job's result.
func (s *Scaler) HandleJobCompleted(_ context.Context, job *scaleset.JobCompleted) error {
	s.defaults()
	name := job.RunnerName
	entry, tracked := s.Fleet.Get(name)
	if !tracked && !strings.HasPrefix(name, VMPrefix) {
		return nil // a runner that is not one of ours
	}
	s.Log.Info("job completed; deleting its VM", "vm", name, "result", job.Result, "repo", s.Repo)
	if tracked {
		s.Fleet.SetState(name, StateDeleting, 0) // the cap slot stays taken until the VM is really gone
	}
	s.wg.Add(1)
	go func() {
		defer s.wg.Done()
		s.destroy(name, entry.RunnerID)
	}()
	return nil
}

// destroy deletes a VM with retries, and removes its runner registration if it never ran a job.
func (s *Scaler) destroy(name string, runnerID int) {
	s.defaults()
	ctx := context.WithoutCancel(s.BaseCtx) // shutting down must not leave a VM running
	var err error
	for attempt := 1; attempt <= s.DeleteRetries; attempt++ {
		if err = s.Cloud.DeleteVM(ctx, name); err == nil {
			break
		}
		s.Log.Warn("could not delete the VM; retrying", "vm", name, "attempt", attempt, "error", err)
		time.Sleep(s.RetryWait)
	}
	if err != nil {
		// Keep the entry, in the deleting state, so the cap still counts the VM and the reaper retries.
		s.Fleet.SetState(name, StateDeleting, 0)
		s.Log.Error("giving up deleting the VM for now; the reaper will retry", "vm", name, "error", err)
		return
	}
	s.Fleet.Release(name)
	if runnerID != 0 {
		if rerr := s.Jit.RemoveRunner(ctx, int64(runnerID)); rerr != nil {
			s.Log.Debug("runner registration not removed (it may already be gone)", "runner", runnerID, "error", rerr)
		}
	}
}

// Shutdown waits for in-flight work and deletes VMs that have no job: creating and idle ones. Busy VMs keep
// running their job; the next broker adopts them, and the reaper is the backstop.
func (s *Scaler) Shutdown(ctx context.Context) {
	s.defaults()
	done := make(chan struct{})
	go func() { s.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-ctx.Done():
		s.Log.Warn("shutdown: gave up waiting for in-flight work")
	}
	for _, e := range s.Fleet.Entries() {
		if e.Repo == s.Repo && e.State != StateBusy && e.State != StateDeleting {
			s.Log.Info("shutdown: deleting a VM with no job", "vm", e.Name, "state", e.State)
			s.destroy(e.Name, e.RunnerID)
		}
	}
}

// Describe is a one-line summary for logs.
func (s *Scaler) Describe() string {
	return fmt.Sprintf("%s: %d VM(s)", s.Repo, s.Fleet.CountRepo(s.Repo))
}
