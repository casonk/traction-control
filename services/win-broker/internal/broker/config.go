// Package broker creates one disposable Windows VM per GitHub Actions job and deletes it afterwards.
//
// A Linux pod holds every credential. For each allowlisted private repository it registers a runner scale set
// (GitHub's actions/scaleset client), waits for demand, asks GitHub for a single-use just-in-time runner
// configuration, creates a VM from the golden image with that configuration as custom data, and deletes the VM
// when the job completes. A reaper deletes any broker VM that outlives its time limit, so a crash cannot leave
// a billing VM behind.
package broker

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Tag keys and values the broker puts on every VM it creates. Only VMs carrying them are ever deleted.
const (
	TagOwner    = "broker"
	TagOwnerVal = "win-broker"
	TagRepo     = "repo"
	TagCreated  = "created"
	// VMPrefix starts every VM name the broker creates (a Windows computer name is at most 15 characters).
	VMPrefix = "wr-"
)

var repoPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)

// AzureConfig names the Azure resources per-job VMs are created in.
type AzureConfig struct {
	SubscriptionID string
	ResourceGroup  string
	Location       string
	SubnetID       string
	ImageID        string
	VMSize         string
	// Credential selects how the broker authenticates: "managed-identity" (default) or "default" for development.
	Credential string
}

// Config is the broker's whole configuration.
type Config struct {
	Repos          []string
	Token          string
	MaxVMs         int
	TTL            time.Duration
	ReapEvery      time.Duration
	ScaleSetSuffix string
	DryRun         bool
	LogLevel       string
	Azure          AzureConfig
}

// ScaleSetName is the `runs-on` label for a repository: <repo>-windows.
func (c Config) ScaleSetName(slug string) string {
	name := slug[strings.LastIndex(slug, "/")+1:]
	name = strings.ToLower(strings.NewReplacer("_", "-", ".", "-").Replace(name))
	return name + c.ScaleSetSuffix
}

// Validate rejects a configuration that could create runaway cost or reach the wrong place.
func (c Config) Validate() error {
	if len(c.Repos) == 0 {
		return fmt.Errorf("no repositories are allowlisted")
	}
	for _, r := range c.Repos {
		if !repoPattern.MatchString(r) {
			return fmt.Errorf("repository %q must look like owner/name", r)
		}
	}
	if c.Token == "" {
		return fmt.Errorf("the GitHub token is empty")
	}
	if c.MaxVMs < 1 || c.MaxVMs > 4 {
		return fmt.Errorf("max VMs must be between 1 and 4, got %d", c.MaxVMs)
	}
	if c.TTL < 5*time.Minute || c.TTL > 6*time.Hour {
		return fmt.Errorf("the VM time limit must be between 5 minutes and 6 hours, got %s", c.TTL)
	}
	if c.ReapEvery < 10*time.Second || c.ReapEvery > c.TTL {
		return fmt.Errorf("the reaper interval must be between 10 seconds and the time limit, got %s", c.ReapEvery)
	}
	if !strings.HasPrefix(c.ScaleSetSuffix, "-") {
		return fmt.Errorf("the scale-set suffix must start with a hyphen")
	}
	if c.DryRun {
		return nil
	}
	a := c.Azure
	for name, value := range map[string]string{
		"subscription id": a.SubscriptionID, "resource group": a.ResourceGroup, "location": a.Location,
		"subnet id": a.SubnetID, "image id": a.ImageID, "VM size": a.VMSize,
	} {
		if value == "" {
			return fmt.Errorf("the Azure %s is not set", name)
		}
	}
	switch a.Credential {
	case "", "managed-identity", "default":
	default:
		return fmt.Errorf("unknown Azure credential %q", a.Credential)
	}
	return nil
}

// LoadFromEnv builds the configuration from WIN_BROKER_* variables and the files they name.
func LoadFromEnv(getenv func(string) string, readFile func(string) ([]byte, error), stat func(string) (os.FileInfo, error)) (Config, error) {
	cfg := Config{
		MaxVMs:         2,
		TTL:            60 * time.Minute,
		ReapEvery:      time.Minute,
		ScaleSetSuffix: "-windows",
		LogLevel:       "info",
		Azure:          AzureConfig{VMSize: "Standard_B2als_v2", Credential: "managed-identity"},
	}
	var errs []string
	if v := getenv("WIN_BROKER_MAX_VMS"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			errs = append(errs, "WIN_BROKER_MAX_VMS: "+err.Error())
		}
		cfg.MaxVMs = n
	}
	if v := getenv("WIN_BROKER_TTL"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			errs = append(errs, "WIN_BROKER_TTL: "+err.Error())
		}
		cfg.TTL = d
	}
	if v := getenv("WIN_BROKER_REAP_EVERY"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			errs = append(errs, "WIN_BROKER_REAP_EVERY: "+err.Error())
		}
		cfg.ReapEvery = d
	}
	if v := getenv("WIN_BROKER_SCALE_SET_SUFFIX"); v != "" {
		cfg.ScaleSetSuffix = v
	}
	if v := getenv("WIN_BROKER_LOG_LEVEL"); v != "" {
		cfg.LogLevel = v
	}
	cfg.DryRun = getenv("WIN_BROKER_DRY_RUN") == "1"
	cfg.Azure.SubscriptionID = getenv("WIN_BROKER_AZURE_SUBSCRIPTION_ID")
	cfg.Azure.ResourceGroup = getenv("WIN_BROKER_AZURE_RESOURCE_GROUP")
	cfg.Azure.Location = getenv("WIN_BROKER_AZURE_LOCATION")
	cfg.Azure.SubnetID = getenv("WIN_BROKER_AZURE_SUBNET_ID")
	cfg.Azure.ImageID = getenv("WIN_BROKER_AZURE_IMAGE_ID")
	if v := getenv("WIN_BROKER_AZURE_VM_SIZE"); v != "" {
		cfg.Azure.VMSize = v
	}
	if v := getenv("WIN_BROKER_AZURE_CREDENTIAL"); v != "" {
		cfg.Azure.Credential = v
	}

	if path := getenv("WIN_BROKER_REPOS_FILE"); path == "" {
		errs = append(errs, "WIN_BROKER_REPOS_FILE is not set")
	} else if data, err := readFile(path); err != nil {
		errs = append(errs, "repository allowlist: "+err.Error())
	} else if repos, err := ParseRepos(data); err != nil {
		errs = append(errs, "repository allowlist: "+err.Error())
	} else {
		cfg.Repos = repos
	}

	if path := getenv("WIN_BROKER_TOKEN_FILE"); path == "" {
		errs = append(errs, "WIN_BROKER_TOKEN_FILE is not set")
	} else {
		if info, err := stat(path); err != nil {
			errs = append(errs, "token file: "+err.Error())
		} else if info.Mode().Perm()&0o007 != 0 {
			// A Kubernetes Secret volume with fsGroup is group-readable, so only "other" access is refused.
			errs = append(errs, fmt.Sprintf("token file %s must not be readable by others (mode %o)", path, info.Mode().Perm()))
		} else if data, err := readFile(path); err != nil {
			errs = append(errs, "token file: "+err.Error())
		} else {
			cfg.Token = strings.TrimSpace(string(data))
		}
	}
	if len(errs) > 0 {
		return cfg, fmt.Errorf("%s", strings.Join(errs, "; "))
	}
	return cfg, cfg.Validate()
}

// ParseRepos reads the allowlist: a JSON array of "owner/name", or one per line (blank lines and # comments ignored).
func ParseRepos(data []byte) ([]string, error) {
	text := strings.TrimSpace(string(data))
	var repos []string
	if strings.HasPrefix(text, "[") {
		if err := json.Unmarshal([]byte(text), &repos); err != nil {
			return nil, err
		}
	} else {
		for _, line := range strings.Split(text, "\n") {
			line = strings.TrimSpace(line)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			repos = append(repos, line)
		}
	}
	seen := map[string]bool{}
	var out []string
	for _, r := range repos {
		r = strings.TrimSpace(r)
		if !repoPattern.MatchString(r) {
			return nil, fmt.Errorf("repository %q must look like owner/name", r)
		}
		if !seen[strings.ToLower(r)] {
			seen[strings.ToLower(r)] = true
			out = append(out, r)
		}
	}
	return out, nil
}
