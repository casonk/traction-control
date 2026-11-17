package broker

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/actions/scaleset"
)

func TestScaleUpCreatesOneVMPerWantedJobWithItsJitConfig(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	s := newScaler(cloud, jit, NewFleet(4))
	got, err := s.HandleDesiredRunnerCount(context.Background(), 2)
	if err != nil || got != 2 {
		t.Fatalf("got %d, %v; want 2, nil", got, err)
	}
	s.wg.Wait()
	if len(cloud.created) != 2 {
		t.Fatalf("created %d VMs, want 2", len(cloud.created))
	}
	for _, spec := range cloud.created {
		if spec.Repo != "o/widgets" || spec.JITConfig != "jit-for-"+spec.Name {
			t.Errorf("unexpected spec %+v", spec)
		}
	}
	if e, _ := s.Fleet.Get(cloud.created[0].Name); e.State != StateIdle || e.RunnerID == 0 {
		t.Errorf("entry after create = %+v, want idle with a runner id", e)
	}
}

func TestDemandAlreadyMetCreatesNothing(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	s := newScaler(cloud, jit, NewFleet(4))
	s.HandleDesiredRunnerCount(context.Background(), 1)
	s.wg.Wait()
	got, _ := s.HandleDesiredRunnerCount(context.Background(), 1)
	s.wg.Wait()
	if got != 1 || len(cloud.created) != 1 {
		t.Fatalf("got %d with %d created, want 1 and 1", got, len(cloud.created))
	}
}

func TestTheGlobalCapIsNeverExceeded(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	fleet := NewFleet(2)
	a, b := newScaler(cloud, jit, fleet), newScaler(cloud, jit, fleet)
	b.Repo = "o/other"
	b.NewName = (&names{n: 10}).next
	a.HandleDesiredRunnerCount(context.Background(), 5)
	b.HandleDesiredRunnerCount(context.Background(), 5)
	a.wg.Wait()
	b.wg.Wait()
	if cloud.count() != 2 || fleet.Count() != 2 {
		t.Fatalf("cloud has %d VMs and fleet %d, want 2 and 2", cloud.count(), fleet.Count())
	}
}

func TestDryRunCreatesNothing(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	s := newScaler(cloud, jit, NewFleet(4))
	s.DryRun = true
	got, _ := s.HandleDesiredRunnerCount(context.Background(), 3)
	s.wg.Wait()
	if got != 0 || cloud.count() != 0 || jit.nextID != 0 {
		t.Fatalf("dry run changed something: got %d, %d VMs, %d configs", got, cloud.count(), jit.nextID)
	}
}

func TestAFailedJitRequestFreesTheSlot(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{err: errors.New("github down")}
	s := newScaler(cloud, jit, NewFleet(1))
	s.HandleDesiredRunnerCount(context.Background(), 1)
	s.wg.Wait()
	if s.Fleet.Count() != 0 || cloud.count() != 0 {
		t.Fatalf("slot not freed: fleet %d, cloud %d", s.Fleet.Count(), cloud.count())
	}
}

func TestAFailedCreateCleansUpPartialResourcesAndTheRunner(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	cloud.createErr = errors.New("quota exceeded")
	s := newScaler(cloud, jit, NewFleet(1))
	s.HandleDesiredRunnerCount(context.Background(), 1)
	s.wg.Wait()
	if cloud.count() != 0 {
		t.Errorf("partial VM left behind: %d", cloud.count())
	}
	if s.Fleet.Count() != 0 {
		t.Errorf("slot not freed")
	}
	if len(jit.removed) != 1 || jit.removed[0] != 101 {
		t.Errorf("runner registration not removed: %v", jit.removed)
	}
}

func TestJobLifecycleDeletesTheVMAndFreesTheSlot(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	s := newScaler(cloud, jit, NewFleet(1))
	s.HandleDesiredRunnerCount(context.Background(), 1)
	s.wg.Wait()
	name := cloud.created[0].Name

	s.HandleJobStarted(context.Background(), &scaleset.JobStarted{RunnerName: name})
	if e, _ := s.Fleet.Get(name); e.State != StateBusy {
		t.Fatalf("state = %s, want busy", e.State)
	}
	s.HandleJobCompleted(context.Background(), &scaleset.JobCompleted{RunnerName: name, Result: "success"})
	s.wg.Wait()
	if cloud.has(name) || s.Fleet.Count() != 0 {
		t.Fatalf("VM or slot left after the job: vm=%v fleet=%d", cloud.has(name), s.Fleet.Count())
	}
}

func TestTheSlotStaysTakenWhileTheVMIsStillBeingDeleted(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	cloud.deleteBlock = make(chan struct{})
	s := newScaler(cloud, jit, NewFleet(1))
	s.HandleDesiredRunnerCount(context.Background(), 1)
	s.wg.Wait()
	name := cloud.created[0].Name
	s.HandleJobCompleted(context.Background(), &scaleset.JobCompleted{RunnerName: name})

	waitFor(t, "the deleting state", func() bool { e, _ := s.Fleet.Get(name); return e.State == StateDeleting })
	if s.Fleet.Count() != 1 {
		t.Fatalf("the cap slot was freed before the VM was gone")
	}
	if got, _ := s.HandleDesiredRunnerCount(context.Background(), 1); got != 0 {
		t.Fatalf("a VM being deleted must not count as serving demand, got %d", got)
	}
	close(cloud.deleteBlock)
	waitFor(t, "the slot to be freed", func() bool { return s.Fleet.Count() == 0 })
}

func TestDeleteRetriesThenKeepsTheEntryForTheReaper(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	s := newScaler(cloud, jit, NewFleet(1))
	s.HandleDesiredRunnerCount(context.Background(), 1)
	s.wg.Wait()
	name := cloud.created[0].Name
	cloud.deleteErrs = 100 // every attempt fails
	s.HandleJobCompleted(context.Background(), &scaleset.JobCompleted{RunnerName: name})
	s.wg.Wait()
	if e, ok := s.Fleet.Get(name); !ok || e.State != StateDeleting {
		t.Fatalf("entry = %+v (ok=%v), want it kept in the deleting state", e, ok)
	}
	if !cloud.has(name) {
		t.Fatal("the VM should still exist")
	}
}

func TestAJobOnAnUntrackedBrokerVMStillDeletesIt(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	s := newScaler(cloud, jit, NewFleet(2))
	cloud.vms["wr-deadbeef"] = VMSpec{Name: "wr-deadbeef", Repo: "o/widgets"}
	s.HandleJobCompleted(context.Background(), &scaleset.JobCompleted{RunnerName: "wr-deadbeef"})
	s.wg.Wait()
	if cloud.has("wr-deadbeef") {
		t.Fatal("a broker-named VM from before a restart was not deleted")
	}
}

func TestAJobOnSomeoneElsesRunnerIsIgnored(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	s := newScaler(cloud, jit, NewFleet(2))
	cloud.vms["build-host-1"] = VMSpec{Name: "build-host-1"}
	s.HandleJobCompleted(context.Background(), &scaleset.JobCompleted{RunnerName: "build-host-1"})
	s.wg.Wait()
	if !cloud.has("build-host-1") {
		t.Fatal("deleted a VM that is not the broker's")
	}
}

func TestShutdownDeletesIdleVMsButKeepsBusyOnes(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	s := newScaler(cloud, jit, NewFleet(3))
	s.HandleDesiredRunnerCount(context.Background(), 2)
	s.wg.Wait()
	busy, idle := cloud.created[0].Name, cloud.created[1].Name
	s.HandleJobStarted(context.Background(), &scaleset.JobStarted{RunnerName: busy})
	s.Shutdown(context.Background())
	if !cloud.has(busy) {
		t.Error("shutdown killed a VM that is running a job")
	}
	if cloud.has(idle) {
		t.Error("shutdown left an idle VM running")
	}
}

func TestAdoptTakesOverOnlyThisRepositorysVMs(t *testing.T) {
	cloud, jit := newFakeCloud(), &fakeJit{}
	s := newScaler(cloud, jit, NewFleet(4))
	s.Adopt([]VM{
		{Name: "wr-aaaaaaaa", Repo: "o/widgets", Created: time.Unix(5, 0)},
		{Name: "wr-bbbbbbbb", Repo: "o/other", Created: time.Unix(5, 0)},
	})
	if _, ok := s.Fleet.Get("wr-aaaaaaaa"); !ok {
		t.Error("own VM not adopted")
	}
	if _, ok := s.Fleet.Get("wr-bbbbbbbb"); ok {
		t.Error("adopted another repository's VM")
	}
}

func TestRandomNamesFitAWindowsComputerName(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 200; i++ {
		n := RandomName()
		if len(n) > 15 || n[:len(VMPrefix)] != VMPrefix {
			t.Fatalf("bad name %q", n)
		}
		if seen[n] {
			t.Fatalf("duplicate name %q", n)
		}
		seen[n] = true
	}
}
