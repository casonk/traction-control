package broker

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/actions/scaleset"
)

type fakeCloud struct {
	mu          sync.Mutex
	vms         map[string]VMSpec
	created     []VMSpec
	deleted     []string
	createErr   error
	deleteErrs  int // fail this many delete calls before succeeding
	deleteBlock chan struct{}
}

func newFakeCloud() *fakeCloud { return &fakeCloud{vms: map[string]VMSpec{}} }

func (c *fakeCloud) CreateVM(_ context.Context, spec VMSpec) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.createErr != nil {
		c.vms[spec.Name] = spec // a failed create can leave partial resources behind
		return c.createErr
	}
	c.vms[spec.Name] = spec
	c.created = append(c.created, spec)
	return nil
}

func (c *fakeCloud) DeleteVM(_ context.Context, name string) error {
	if c.deleteBlock != nil {
		<-c.deleteBlock
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.deleteErrs > 0 {
		c.deleteErrs--
		return errors.New("azure unavailable")
	}
	delete(c.vms, name)
	c.deleted = append(c.deleted, name)
	return nil
}

func (c *fakeCloud) ListVMs(context.Context) ([]VM, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	var out []VM
	for _, s := range c.vms {
		out = append(out, VM{Name: s.Name, Repo: s.Repo, Created: s.Created})
	}
	return out, nil
}

func (c *fakeCloud) has(name string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.vms[name]
	return ok
}

func (c *fakeCloud) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.vms)
}

type fakeJit struct {
	mu      sync.Mutex
	err     error
	nextID  int
	removed []int64
}

func (j *fakeJit) GenerateJitRunnerConfig(_ context.Context, s *scaleset.RunnerScaleSetJitRunnerSetting, _ int) (*scaleset.RunnerScaleSetJitRunnerConfig, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.err != nil {
		return nil, j.err
	}
	j.nextID++
	return &scaleset.RunnerScaleSetJitRunnerConfig{
		Runner:           &scaleset.RunnerReference{ID: 100 + j.nextID, Name: s.Name},
		EncodedJITConfig: "jit-for-" + s.Name,
	}, nil
}

func (j *fakeJit) RemoveRunner(_ context.Context, id int64) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	j.removed = append(j.removed, id)
	return nil
}

type names struct {
	mu     sync.Mutex
	n      int
	prefix string
}

func (g *names) next() string {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.n++
	prefix := g.prefix
	if prefix == "" {
		prefix = VMPrefix
	}
	return prefix + string(rune('a'+g.n-1)) + "000000"
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

func newScaler(cloud *fakeCloud, jit *fakeJit, fleet *Fleet) *Scaler {
	g := &names{}
	return &Scaler{
		Repo: "o/widgets", ScaleSetID: 7, Jit: jit, Cloud: cloud, Fleet: fleet,
		NewName: g.next, Now: func() time.Time { return time.Unix(1_000_000, 0) },
		RetryWait: time.Millisecond, DeleteRetries: 3,
	}
}
