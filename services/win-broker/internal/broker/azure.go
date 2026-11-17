package broker

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"math/big"
	"net/http"
	"regexp"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore"
	"github.com/Azure/azure-sdk-for-go/sdk/azcore/to"
	"github.com/Azure/azure-sdk-for-go/sdk/azidentity"
	"github.com/Azure/azure-sdk-for-go/sdk/resourcemanager/compute/armcompute/v7"
)

// AzureCloud creates and deletes per-job VMs. Each VM's OS disk, NIC and public IP are created with the VM and use
// delete option "Delete", so deleting the VM removes all of them.
type AzureCloud struct {
	cfg AzureConfig
	vms *armcompute.VirtualMachinesClient
}

var _ Cloud = (*AzureCloud)(nil)

// NewAzureCloud authenticates with the VM's managed identity (or the default chain for development).
func NewAzureCloud(cfg AzureConfig) (*AzureCloud, error) {
	var cred azcore.TokenCredential
	var err error
	if cfg.Credential == "default" {
		cred, err = azidentity.NewDefaultAzureCredential(nil)
	} else {
		cred, err = azidentity.NewManagedIdentityCredential(nil)
	}
	if err != nil {
		return nil, fmt.Errorf("azure credential: %w", err)
	}
	vms, err := armcompute.NewVirtualMachinesClient(cfg.SubscriptionID, cred, nil)
	if err != nil {
		return nil, fmt.Errorf("azure compute client: %w", err)
	}
	return &AzureCloud{cfg: cfg, vms: vms}, nil
}

var jitPattern = regexp.MustCompile(`^[A-Za-z0-9+/=_-]{20,}$`)

// LinuxUserData is the cloud-init script a per-job Linux VM runs at first boot: it starts the runner baked into the
// image with the single-use configuration. The configuration is base64 text, and anything else is refused, so it can
// never carry shell syntax into the script.
func LinuxUserData(jit string) (string, error) {
	if !jitPattern.MatchString(jit) {
		return "", fmt.Errorf("the runner configuration is not base64 text; refusing to put it in a script")
	}
	return "#!/bin/bash\nset -eu\ncd /opt/actions-runner\nexec runuser -u runner -- ./run.sh --jitconfig '" + jit + "'\n", nil
}

// BuildVM returns the VM definition for a spec. It is separate from CreateVM so it can be checked without Azure.
func (a *AzureCloud) BuildVM(spec VMSpec, adminPassword string) (armcompute.VirtualMachine, error) {
	del := armcompute.DeleteOptionsDelete
	class := classOf(spec.Class)
	imageID, size := a.cfg.ImageID, a.cfg.VMSize
	customData := spec.JITConfig // Windows: the start task reads it from CustomData.bin
	osProfile := &armcompute.OSProfile{
		ComputerName:  to.Ptr(spec.Name),
		AdminUsername: to.Ptr("runneradmin"),
		AdminPassword: to.Ptr(adminPassword),
	}
	if class == ClassLinux {
		imageID, size = a.cfg.LinuxImageID, a.cfg.LinuxVMSize
		script, err := LinuxUserData(spec.JITConfig)
		if err != nil {
			return armcompute.VirtualMachine{}, err
		}
		customData = script
		// Nothing logs in (no inbound port is open); a password only has to exist.
		osProfile.LinuxConfiguration = &armcompute.LinuxConfiguration{
			DisablePasswordAuthentication: to.Ptr(false),
			ProvisionVMAgent:              to.Ptr(true),
		}
	} else {
		osProfile.WindowsConfiguration = &armcompute.WindowsConfiguration{
			ProvisionVMAgent:       to.Ptr(true),
			EnableAutomaticUpdates: to.Ptr(false),
		}
	}
	// The runner configuration is single use and expires unused; the VM is deleted after one job.
	osProfile.CustomData = to.Ptr(base64.StdEncoding.EncodeToString([]byte(customData)))
	return armcompute.VirtualMachine{
		Location: to.Ptr(a.cfg.Location),
		Tags: map[string]*string{
			TagOwner:   to.Ptr(TagOwnerVal),
			TagRepo:    to.Ptr(spec.Repo),
			TagCreated: to.Ptr(spec.Created.UTC().Format(time.RFC3339)),
			TagClass:   to.Ptr(string(class)),
		},
		Properties: &armcompute.VirtualMachineProperties{
			HardwareProfile: &armcompute.HardwareProfile{VMSize: to.Ptr(armcompute.VirtualMachineSizeTypes(size))},
			StorageProfile: &armcompute.StorageProfile{
				ImageReference: &armcompute.ImageReference{ID: to.Ptr(imageID)},
				OSDisk: &armcompute.OSDisk{
					Name:         to.Ptr(spec.Name + "-osdisk"),
					CreateOption: to.Ptr(armcompute.DiskCreateOptionTypesFromImage),
					DeleteOption: to.Ptr(armcompute.DiskDeleteOptionTypesDelete),
					ManagedDisk:  &armcompute.ManagedDiskParameters{StorageAccountType: to.Ptr(armcompute.StorageAccountTypesStandardSSDLRS)},
				},
			},
			OSProfile: osProfile,
			NetworkProfile: &armcompute.NetworkProfile{
				NetworkAPIVersion: to.Ptr(armcompute.NetworkAPIVersionTwoThousandTwenty1101),
				NetworkInterfaceConfigurations: []*armcompute.VirtualMachineNetworkInterfaceConfiguration{{
					Name: to.Ptr(spec.Name + "-nic"),
					Properties: &armcompute.VirtualMachineNetworkInterfaceConfigurationProperties{
						Primary:      to.Ptr(true),
						DeleteOption: to.Ptr(del),
						IPConfigurations: []*armcompute.VirtualMachineNetworkInterfaceIPConfiguration{{
							Name: to.Ptr("ipconfig"),
							Properties: &armcompute.VirtualMachineNetworkInterfaceIPConfigurationProperties{
								Primary: to.Ptr(true),
								Subnet:  &armcompute.SubResource{ID: to.Ptr(a.cfg.SubnetID)},
								// Outbound only: the subnet's security group denies all inbound traffic.
								PublicIPAddressConfiguration: &armcompute.VirtualMachinePublicIPAddressConfiguration{
									Name: to.Ptr(spec.Name + "-pip"),
									SKU: &armcompute.PublicIPAddressSKU{
										Name: to.Ptr(armcompute.PublicIPAddressSKUNameStandard),
										Tier: to.Ptr(armcompute.PublicIPAddressSKUTierRegional),
									},
									Properties: &armcompute.VirtualMachinePublicIPAddressConfigurationProperties{
										DeleteOption:             to.Ptr(del),
										PublicIPAllocationMethod: to.Ptr(armcompute.PublicIPAllocationMethodStatic),
									},
								},
							},
						}},
					},
				}},
			},
		},
	}, nil
}

// CreateVM creates the VM and waits until Azure reports it provisioned.
func (a *AzureCloud) CreateVM(ctx context.Context, spec VMSpec) error {
	password, err := RandomPassword()
	if err != nil {
		return err
	}
	vm, err := a.BuildVM(spec, password)
	if err != nil {
		return fmt.Errorf("define VM %s: %w", spec.Name, err)
	}
	poller, err := a.vms.BeginCreateOrUpdate(ctx, a.cfg.ResourceGroup, spec.Name, vm, nil)
	if err != nil {
		return fmt.Errorf("create VM %s: %w", spec.Name, err)
	}
	if _, err := poller.PollUntilDone(ctx, &azcoreFreq); err != nil {
		return fmt.Errorf("wait for VM %s: %w", spec.Name, err)
	}
	return nil
}

// DeleteVM deletes a broker VM and, through the delete options, its disk, NIC and public IP. A VM that is already
// gone is not an error. It refuses to touch a VM that does not carry the broker's tag.
func (a *AzureCloud) DeleteVM(ctx context.Context, name string) error {
	got, err := a.vms.Get(ctx, a.cfg.ResourceGroup, name, nil)
	if err != nil {
		if isNotFound(err) {
			return nil
		}
		return fmt.Errorf("look up VM %s: %w", name, err)
	}
	if v := got.Tags[TagOwner]; v == nil || *v != TagOwnerVal {
		return fmt.Errorf("refusing to delete VM %s: it is not tagged %s=%s", name, TagOwner, TagOwnerVal)
	}
	poller, err := a.vms.BeginDelete(ctx, a.cfg.ResourceGroup, name, &armcompute.VirtualMachinesClientBeginDeleteOptions{ForceDeletion: to.Ptr(true)})
	if err != nil {
		if isNotFound(err) {
			return nil
		}
		return fmt.Errorf("delete VM %s: %w", name, err)
	}
	if _, err := poller.PollUntilDone(ctx, &azcoreFreq); err != nil && !isNotFound(err) {
		return fmt.Errorf("wait for deletion of VM %s: %w", name, err)
	}
	return nil
}

// ListVMs lists the resource group's VMs that carry the broker's tag.
func (a *AzureCloud) ListVMs(ctx context.Context) ([]VM, error) {
	var out []VM
	pager := a.vms.NewListPager(a.cfg.ResourceGroup, nil)
	for pager.More() {
		page, err := pager.NextPage(ctx)
		if err != nil {
			return nil, fmt.Errorf("list VMs: %w", err)
		}
		for _, v := range page.Value {
			if v.Name == nil || v.Tags[TagOwner] == nil || *v.Tags[TagOwner] != TagOwnerVal {
				continue
			}
			vm := VM{Name: *v.Name}
			if r := v.Tags[TagRepo]; r != nil {
				vm.Repo = *r
			}
			vm.Class = ClassWindows // VMs created before the class tag existed were all Windows
			if c := v.Tags[TagClass]; c != nil && Class(*c) == ClassLinux {
				vm.Class = ClassLinux
			}
			if c := v.Tags[TagCreated]; c != nil {
				if t, err := time.Parse(time.RFC3339, *c); err == nil {
					vm.Created = t
				}
			}
			if vm.Created.IsZero() {
				vm.Created = time.Unix(0, 0) // no creation time: treat as old so the reaper removes it
			}
			out = append(out, vm)
		}
	}
	return out, nil
}

func isNotFound(err error) bool {
	var re *azcore.ResponseError
	return errors.As(err, &re) && re.StatusCode == http.StatusNotFound
}

// RandomPassword returns a 24-character password with all four character classes. Nothing ever logs in with it
// (no inbound port is open); Windows only requires that one exists. It is never stored.
func RandomPassword() (string, error) {
	const (
		lower  = "abcdefghijkmnopqrstuvwxyz"
		upper  = "ABCDEFGHJKLMNPQRSTUVWXYZ"
		digits = "23456789"
		symbol = "!#%*-_=+"
	)
	all := lower + upper + digits + symbol
	pick := func(set string) (byte, error) {
		n, err := rand.Int(rand.Reader, big.NewInt(int64(len(set))))
		if err != nil {
			return 0, err
		}
		return set[n.Int64()], nil
	}
	out := make([]byte, 0, 24)
	for _, set := range []string{lower, upper, digits, symbol} {
		c, err := pick(set)
		if err != nil {
			return "", err
		}
		out = append(out, c)
	}
	for len(out) < 24 {
		c, err := pick(all)
		if err != nil {
			return "", err
		}
		out = append(out, c)
	}
	// Shuffle so the guaranteed characters are not always first.
	for i := len(out) - 1; i > 0; i-- {
		n, err := rand.Int(rand.Reader, big.NewInt(int64(i+1)))
		if err != nil {
			return "", err
		}
		j := int(n.Int64())
		out[i], out[j] = out[j], out[i]
	}
	return string(out), nil
}
