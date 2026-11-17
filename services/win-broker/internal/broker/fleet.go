package broker

import (
	"sync"
	"time"
)

// Runner states.
const (
	StateCreating = "creating"
	StateIdle     = "idle"
	StateBusy     = "busy"
	// StateDeleting keeps the cap slot taken until the cloud confirms the VM is gone.
	StateDeleting = "deleting"
)

// Entry is one VM the broker is responsible for.
type Entry struct {
	Name     string
	Repo     string
	Class    Class
	RunnerID int
	State    string
	Created  time.Time
}

// Fleet is the broker's record of its VMs and the one place the global cap is enforced.
type Fleet struct {
	mu  sync.Mutex
	max int
	vms map[string]*Entry
}

// NewFleet returns an empty fleet that never holds more than max VMs.
func NewFleet(max int) *Fleet { return &Fleet{max: max, vms: map[string]*Entry{}} }

// Reserve records a VM about to be created. It reports false when the cap is reached or the name is taken.
func (f *Fleet) Reserve(name, repo string, class Class, now time.Time) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, taken := f.vms[name]; taken || len(f.vms) >= f.max {
		return false
	}
	f.vms[name] = &Entry{Name: name, Repo: repo, Class: class, State: StateCreating, Created: now}
	return true
}

// Adopt records a VM found in the cloud after a restart. The cap is not applied: it exists already.
func (f *Fleet) Adopt(name, repo string, class Class, created time.Time) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.vms[name]; !ok {
		f.vms[name] = &Entry{Name: name, Repo: repo, Class: class, State: StateIdle, Created: created}
	}
}

// Release forgets a VM and returns what was recorded.
func (f *Fleet) Release(name string) (Entry, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	e, ok := f.vms[name]
	if !ok {
		return Entry{}, false
	}
	delete(f.vms, name)
	return *e, true
}

// Get returns a copy of one entry.
func (f *Fleet) Get(name string) (Entry, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	e, ok := f.vms[name]
	if !ok {
		return Entry{}, false
	}
	return *e, true
}

// SetState moves a VM to a new state and optionally records its runner id.
func (f *Fleet) SetState(name, state string, runnerID int) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	e, ok := f.vms[name]
	if !ok {
		return false
	}
	e.State = state
	if runnerID != 0 {
		e.RunnerID = runnerID
	}
	return true
}

// Transition moves a VM from one state to another only if it is still in the first.
func (f *Fleet) Transition(name, from, to string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	e, ok := f.vms[name]
	if !ok || e.State != from {
		return false
	}
	e.State = to
	return true
}

// Entries returns a copy of every entry.
func (f *Fleet) Entries() []Entry {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]Entry, 0, len(f.vms))
	for _, e := range f.vms {
		out = append(out, *e)
	}
	return out
}

// Count is the number of VMs, all repositories.
func (f *Fleet) Count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.vms)
}

// CountRepo is the number of VMs of one class serving one repository, not counting ones already being deleted.
func (f *Fleet) CountRepo(repo string, class Class) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, e := range f.vms {
		if e.Repo == repo && e.Class == class && e.State != StateDeleting {
			n++
		}
	}
	return n
}

// Max is the cap.
func (f *Fleet) Max() int { return f.max }
