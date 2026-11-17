package broker

import (
	"context"
	"log/slog"
)

// LogCloud is the dry-run cloud: it creates nothing and lists nothing.
type LogCloud struct{ Log *slog.Logger }

var _ Cloud = LogCloud{}

// CreateVM only logs.
func (c LogCloud) CreateVM(_ context.Context, spec VMSpec) error {
	c.Log.Info("dry run: would create a VM", "vm", spec.Name, "repo", spec.Repo)
	return nil
}

// DeleteVM only logs.
func (c LogCloud) DeleteVM(_ context.Context, name string) error {
	c.Log.Info("dry run: would delete a VM", "vm", name)
	return nil
}

// ListVMs returns nothing.
func (LogCloud) ListVMs(context.Context) ([]VM, error) { return nil, nil }
