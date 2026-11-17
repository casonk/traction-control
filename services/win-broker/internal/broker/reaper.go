package broker

import (
	"context"
	"log/slog"
	"time"
)

// Reaper is the cost backstop. It deletes any broker VM older than the time limit whatever the broker believes about
// it, and forgets fleet entries whose VM no longer exists, so a crash or a missed event cannot leave a VM billing
// or a cap slot taken.
type Reaper struct {
	Cloud Cloud
	Fleet *Fleet
	TTL   time.Duration
	Every time.Duration
	Log   *slog.Logger
	Now   func() time.Time
	// CreateGrace is how long a VM may stay in the creating state before its slot is reclaimed.
	CreateGrace time.Duration
}

// Sweep runs one pass and returns the names of the VMs it deleted.
func (r *Reaper) Sweep(ctx context.Context) ([]string, error) {
	now := time.Now
	if r.Now != nil {
		now = r.Now
	}
	log := r.Log
	if log == nil {
		log = slog.New(slog.DiscardHandler)
	}
	grace := r.CreateGrace
	if grace == 0 {
		grace = 20 * time.Minute
	}
	vms, err := r.Cloud.ListVMs(ctx)
	if err != nil {
		return nil, err
	}
	exists := map[string]bool{}
	var deleted []string
	for _, vm := range vms {
		exists[vm.Name] = true
		if age := now().Sub(vm.Created); age > r.TTL {
			log.Warn("deleting a VM past its time limit", "vm", vm.Name, "age", age.Round(time.Second).String(), "repo", vm.Repo)
			if err := r.Cloud.DeleteVM(ctx, vm.Name); err != nil {
				log.Error("could not delete the VM", "vm", vm.Name, "error", err)
				continue
			}
			r.Fleet.Release(vm.Name)
			delete(exists, vm.Name)
			deleted = append(deleted, vm.Name)
		}
	}
	for _, e := range r.Fleet.Entries() {
		switch {
		case e.State == StateDeleting && exists[e.Name]:
			log.Info("retrying a VM deletion that did not finish", "vm", e.Name)
			if err := r.Cloud.DeleteVM(ctx, e.Name); err != nil {
				log.Error("could not delete the VM", "vm", e.Name, "error", err)
				continue
			}
			r.Fleet.Release(e.Name)
			deleted = append(deleted, e.Name)
		case e.State == StateCreating && now().Sub(e.Created) > grace:
			log.Warn("reclaiming a slot stuck in creating", "vm", e.Name)
			r.Fleet.Release(e.Name)
		case e.State != StateCreating && !exists[e.Name]:
			log.Info("forgetting a VM that no longer exists", "vm", e.Name)
			r.Fleet.Release(e.Name)
		}
	}
	return deleted, nil
}

// Run sweeps every interval until the context ends.
func (r *Reaper) Run(ctx context.Context) {
	log := r.Log
	if log == nil {
		log = slog.New(slog.DiscardHandler)
	}
	ticker := time.NewTicker(r.Every)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if _, err := r.Sweep(ctx); err != nil {
				log.Error("reaper sweep failed", "error", err)
			}
		}
	}
}
