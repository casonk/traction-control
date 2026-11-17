package broker

import (
	"encoding/base64"
	"strings"
	"testing"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/resourcemanager/compute/armcompute/v7"
)

func testAzure() *AzureCloud {
	return &AzureCloud{cfg: AzureConfig{
		Location: "northcentralus", SubnetID: "/subscriptions/x/subnets/s", ImageID: "/subscriptions/x/images/windows-runner/versions/1.0.0",
		VMSize: "Standard_B2als_v2", ResourceGroup: "rg",
	}}
}

func TestBuiltVMCarriesTheOwnershipTagsAndTheJitConfig(t *testing.T) {
	spec := VMSpec{Name: "wr-1a2b3c4d", Repo: "o/widgets", Created: time.Date(2026, 11, 17, 12, 0, 0, 0, time.UTC), JITConfig: "the-jit-config"}
	vm := testAzure().BuildVM(spec, "Pa55w0rd!")
	if *vm.Tags[TagOwner] != TagOwnerVal || *vm.Tags[TagRepo] != "o/widgets" || *vm.Tags[TagCreated] != "2026-11-17T12:00:00Z" {
		t.Errorf("tags = %v", vm.Tags)
	}
	got, err := base64.StdEncoding.DecodeString(*vm.Properties.OSProfile.CustomData)
	if err != nil || string(got) != "the-jit-config" {
		t.Errorf("custom data = %q, %v", got, err)
	}
	if n := *vm.Properties.OSProfile.ComputerName; len(n) > 15 {
		t.Errorf("computer name %q is longer than 15 characters", n)
	}
	if *vm.Properties.StorageProfile.ImageReference.ID != testAzure().cfg.ImageID {
		t.Error("the VM is not created from the golden image")
	}
}

func TestEverythingCreatedWithTheVMIsDeletedWithIt(t *testing.T) {
	vm := testAzure().BuildVM(VMSpec{Name: "wr-1a2b3c4d", JITConfig: "x"}, "pw")
	if *vm.Properties.StorageProfile.OSDisk.DeleteOption != armcompute.DiskDeleteOptionTypesDelete {
		t.Error("the OS disk would outlive the VM")
	}
	nic := vm.Properties.NetworkProfile.NetworkInterfaceConfigurations[0]
	if *nic.Properties.DeleteOption != armcompute.DeleteOptionsDelete {
		t.Error("the NIC would outlive the VM")
	}
	pip := nic.Properties.IPConfigurations[0].Properties.PublicIPAddressConfiguration
	if *pip.Properties.DeleteOption != armcompute.DeleteOptionsDelete {
		t.Error("the public IP would outlive the VM")
	}
	if *nic.Properties.IPConfigurations[0].Properties.Subnet.ID != testAzure().cfg.SubnetID {
		t.Error("the VM is not in the locked-down subnet")
	}
}

func TestPasswordsMeetWindowsComplexityAndDiffer(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 50; i++ {
		p, err := RandomPassword()
		if err != nil || len(p) != 24 {
			t.Fatalf("password %q, %v", p, err)
		}
		var l, u, d, s bool
		for _, c := range p {
			switch {
			case c >= 'a' && c <= 'z':
				l = true
			case c >= 'A' && c <= 'Z':
				u = true
			case c >= '0' && c <= '9':
				d = true
			default:
				s = true
			}
		}
		if !(l && u && d && s) {
			t.Fatalf("password %q is missing a character class", p)
		}
		if seen[p] {
			t.Fatal("a password repeated")
		}
		seen[p] = true
		if strings.ContainsAny(p, " \"'\\") {
			t.Fatalf("password %q has a character that is awkward to pass on", p)
		}
	}
}
