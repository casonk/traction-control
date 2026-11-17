package broker

import (
	"context"
	"time"
)

// VMSpec describes one per-job VM.
type VMSpec struct {
	Name    string
	Repo    string
	Created time.Time
	// JITConfig is the single-use runner configuration. The cloud delivers it as the VM's custom data.
	JITConfig string
}

// VM is a broker VM as listed from the cloud.
type VM struct {
	Name    string
	Repo    string
	Created time.Time
}

// Cloud is the part of Azure the broker needs. Deleting a VM must also remove its disk, NIC and public IP.
type Cloud interface {
	CreateVM(ctx context.Context, spec VMSpec) error
	DeleteVM(ctx context.Context, name string) error
	// ListVMs returns only VMs carrying the broker's ownership tag.
	ListVMs(ctx context.Context) ([]VM, error)
}
