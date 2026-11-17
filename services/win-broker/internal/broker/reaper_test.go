package broker

import (
	"context"
	"testing"
	"time"
)

func reaperAt(cloud *fakeCloud, fleet *Fleet, now time.Time) *Reaper {
	return &Reaper{Cloud: cloud, Fleet: fleet, TTL: time.Hour, Every: time.Minute, Now: func() time.Time { return now }}
}

func TestReaperDeletesOnlyVMsPastTheTimeLimit(t *testing.T) {
	now := time.Unix(10_000_000, 0)
	cloud, fleet := newFakeCloud(), NewFleet(4)
	cloud.vms["wr-old00000"] = VMSpec{Name: "wr-old00000", Created: now.Add(-2 * time.Hour)}
	cloud.vms["wr-new00000"] = VMSpec{Name: "wr-new00000", Created: now.Add(-10 * time.Minute)}
	fleet.Adopt("wr-old00000", "o/a", now.Add(-2*time.Hour))
	fleet.Adopt("wr-new00000", "o/a", now.Add(-10*time.Minute))

	deleted, err := reaperAt(cloud, fleet, now).Sweep(context.Background())
	if err != nil || len(deleted) != 1 || deleted[0] != "wr-old00000" {
		t.Fatalf("deleted %v, %v; want only the old VM", deleted, err)
	}
	if cloud.has("wr-old00000") || !cloud.has("wr-new00000") {
		t.Error("wrong VM deleted")
	}
	if _, ok := fleet.Get("wr-old00000"); ok {
		t.Error("the reaped VM's slot was not freed")
	}
}

func TestReaperReclaimsASlotStuckInCreating(t *testing.T) {
	now := time.Unix(10_000_000, 0)
	cloud, fleet := newFakeCloud(), NewFleet(1)
	fleet.Reserve("wr-stuck000", "o/a", now.Add(-30*time.Minute))
	reaperAt(cloud, fleet, now).Sweep(context.Background())
	if fleet.Count() != 0 {
		t.Fatal("a slot stuck in creating was never reclaimed")
	}
	fleet.Reserve("wr-fresh000", "o/a", now.Add(-time.Minute))
	reaperAt(cloud, fleet, now).Sweep(context.Background())
	if fleet.Count() != 1 {
		t.Fatal("a VM that is still being created lost its slot")
	}
}

func TestReaperForgetsAVMThatNoLongerExists(t *testing.T) {
	now := time.Unix(10_000_000, 0)
	cloud, fleet := newFakeCloud(), NewFleet(2)
	fleet.Adopt("wr-gone0000", "o/a", now)
	reaperAt(cloud, fleet, now).Sweep(context.Background())
	if fleet.Count() != 0 {
		t.Fatal("an entry for a VM that is gone was kept")
	}
}

func TestReaperRetriesADeletionThatDidNotFinish(t *testing.T) {
	now := time.Unix(10_000_000, 0)
	cloud, fleet := newFakeCloud(), NewFleet(2)
	cloud.vms["wr-stuck000"] = VMSpec{Name: "wr-stuck000", Created: now}
	fleet.Adopt("wr-stuck000", "o/a", now)
	fleet.SetState("wr-stuck000", StateDeleting, 0)
	deleted, _ := reaperAt(cloud, fleet, now).Sweep(context.Background())
	if len(deleted) != 1 || cloud.has("wr-stuck000") || fleet.Count() != 0 {
		t.Fatalf("deletion not retried: deleted=%v vm=%v fleet=%d", deleted, cloud.has("wr-stuck000"), fleet.Count())
	}
}

func TestReaperTreatsAVMWithNoCreationTimeAsOld(t *testing.T) {
	now := time.Unix(10_000_000, 0)
	cloud, fleet := newFakeCloud(), NewFleet(2)
	cloud.vms["wr-notime00"] = VMSpec{Name: "wr-notime00", Created: time.Unix(0, 0)}
	deleted, _ := reaperAt(cloud, fleet, now).Sweep(context.Background())
	if len(deleted) != 1 {
		t.Fatal("a VM with no usable creation time was not reaped")
	}
}
