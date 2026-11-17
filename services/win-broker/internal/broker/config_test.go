package broker

import (
	"errors"
	"io/fs"
	"os"
	"strings"
	"testing"
	"time"
)

type fakeInfo struct{ mode fs.FileMode }

func (f fakeInfo) Name() string       { return "token" }
func (f fakeInfo) Size() int64        { return 1 }
func (f fakeInfo) Mode() fs.FileMode  { return f.mode }
func (f fakeInfo) ModTime() time.Time { return time.Time{} }
func (f fakeInfo) IsDir() bool        { return false }
func (f fakeInfo) Sys() any           { return nil }

func load(env map[string]string, tokenMode fs.FileMode) (Config, error) {
	files := map[string]string{"/repos": "o/widgets\n# a comment\no/other\n", "/token": " ghp-secret \n"}
	return LoadFromEnv(
		func(k string) string { return env[k] },
		func(p string) ([]byte, error) {
			if v, ok := files[p]; ok {
				return []byte(v), nil
			}
			return nil, errors.New("no such file")
		},
		func(string) (os.FileInfo, error) { return fakeInfo{tokenMode}, nil },
	)
}

func goodEnv() map[string]string {
	return map[string]string{
		"WIN_BROKER_REPOS_FILE": "/repos", "WIN_BROKER_TOKEN_FILE": "/token",
		"WIN_BROKER_AZURE_SUBSCRIPTION_ID": "sub", "WIN_BROKER_AZURE_RESOURCE_GROUP": "rg",
		"WIN_BROKER_AZURE_LOCATION": "northcentralus", "WIN_BROKER_AZURE_SUBNET_ID": "subnet",
		"WIN_BROKER_AZURE_IMAGE_ID": "image",
	}
}

func TestAGoodConfigLoadsWithSafeDefaults(t *testing.T) {
	cfg, err := load(goodEnv(), 0o400)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.MaxVMs != 2 || cfg.TTL != time.Hour || cfg.Token != "ghp-secret" || len(cfg.Repos) != 2 {
		t.Errorf("unexpected config %+v", cfg)
	}
	if got := cfg.ScaleSetName("o/Some_Repo.x"); got != "some-repo-x-windows" {
		t.Errorf("scale set name = %q", got)
	}
}

func TestATokenFileOthersCanReadIsRefusedButAGroupMountIsAllowed(t *testing.T) {
	_, err := load(goodEnv(), 0o644)
	if err == nil || !strings.Contains(err.Error(), "must not be readable") {
		t.Fatalf("err = %v", err)
	}
	if _, err := load(goodEnv(), 0o440); err != nil {
		t.Fatalf("a Secret volume (group-readable) was refused: %v", err)
	}
}

func TestLimitsAreEnforced(t *testing.T) {
	for name, kv := range map[string][2]string{
		"too many VMs":     {"WIN_BROKER_MAX_VMS", "9"},
		"zero VMs":         {"WIN_BROKER_MAX_VMS", "0"},
		"ttl too short":    {"WIN_BROKER_TTL", "1m"},
		"ttl too long":     {"WIN_BROKER_TTL", "48h"},
		"reaper too slow":  {"WIN_BROKER_REAP_EVERY", "3h"},
		"not a number":     {"WIN_BROKER_MAX_VMS", "two"},
		"bad credential":   {"WIN_BROKER_AZURE_CREDENTIAL", "password"},
		"bad suffix":       {"WIN_BROKER_SCALE_SET_SUFFIX", "windows"},
		"missing resource": {"WIN_BROKER_AZURE_IMAGE_ID", ""},
	} {
		env := goodEnv()
		env[kv[0]] = kv[1]
		if _, err := load(env, 0o400); err == nil {
			t.Errorf("%s: expected an error", name)
		}
	}
}

func TestDryRunNeedsNoAzureSettings(t *testing.T) {
	env := map[string]string{"WIN_BROKER_REPOS_FILE": "/repos", "WIN_BROKER_TOKEN_FILE": "/token", "WIN_BROKER_DRY_RUN": "1"}
	cfg, err := load(env, 0o400)
	if err != nil || !cfg.DryRun {
		t.Fatalf("%v %+v", err, cfg)
	}
}

func TestParseReposAcceptsJSONOrLinesAndRejectsJunk(t *testing.T) {
	got, err := ParseRepos([]byte(`["o/a","O/A","o/b"]`))
	if err != nil || len(got) != 2 {
		t.Fatalf("json: %v %v", got, err)
	}
	got, err = ParseRepos([]byte("o/a\n\n# note\no/b\n"))
	if err != nil || len(got) != 2 {
		t.Fatalf("lines: %v %v", got, err)
	}
	for _, bad := range []string{"just-a-name", "o/a b", "https://github.com/o/a", "o/a/b"} {
		if _, err := ParseRepos([]byte(bad)); err == nil {
			t.Errorf("%q was accepted", bad)
		}
	}
}

func TestEmptyAllowlistIsAnError(t *testing.T) {
	c := Config{Token: "t", MaxVMs: 1, TTL: time.Hour, ReapEvery: time.Minute, ScaleSetSuffix: "-windows", DryRun: true}
	if err := c.Validate(); err == nil {
		t.Fatal("an empty allowlist must not start the broker")
	}
}
