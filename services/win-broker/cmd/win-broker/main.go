// Command win-broker runs disposable Windows runners for private repositories: one VM per job, deleted afterwards.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/actions/scaleset"
	"github.com/actions/scaleset/listener"

	"github.com/casonk/traction-control/services/win-broker/internal/broker"
)

var version = "dev"

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "win-broker: %v\n", err)
		os.Exit(1)
	}
}

func newLogger(level string) *slog.Logger {
	var l slog.Level
	switch strings.ToLower(level) {
	case "debug":
		l = slog.LevelDebug
	case "warn":
		l = slog.LevelWarn
	case "error":
		l = slog.LevelError
	default:
		l = slog.LevelInfo
	}
	return slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: l}))
}

func run() error {
	cfg, err := broker.LoadFromEnv(os.Getenv, os.ReadFile, os.Stat)
	if err != nil {
		return err
	}
	log := newLogger(cfg.LogLevel)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	var cloud broker.Cloud
	if cfg.DryRun {
		log.Warn("dry run: no VM will be created or deleted")
		cloud = broker.LogCloud{Log: log}
	} else if cloud, err = broker.NewAzureCloud(cfg.Azure); err != nil {
		return err
	}

	fleet := broker.NewFleet(cfg.MaxVMs)
	existing, err := cloud.ListVMs(ctx)
	if err != nil {
		return fmt.Errorf("cannot list existing VMs (is the managed identity assigned?): %w", err)
	}
	if cfg.LinuxVM {
		log.Warn("the premium per-job Linux VM class is enabled")
	}
	log.Info("starting", "version", version, "repos", len(cfg.Repos), "maxVMs", cfg.MaxVMs, "ttl", cfg.TTL.String(), "existingVMs", len(existing))

	reaper := &broker.Reaper{Cloud: cloud, Fleet: fleet, TTL: cfg.TTL, Every: cfg.ReapEvery, Log: log.With("component", "reaper")}
	var wg sync.WaitGroup
	wg.Add(1)
	go func() { defer wg.Done(); reaper.Run(ctx) }()

	scalers := make([]*broker.Scaler, 0, len(cfg.Repos)*2)
	for _, slug := range cfg.Repos {
		for _, class := range cfg.Classes() {
			s := &broker.Scaler{
				Repo: slug, Class: class, Cloud: cloud, Fleet: fleet, DryRun: cfg.DryRun,
				Log: log.With("repo", slug, "class", string(class)), BaseCtx: ctx,
			}
			s.Adopt(existing)
			scalers = append(scalers, s)
			wg.Add(1)
			go func() { defer wg.Done(); serve(ctx, cfg, s, s.Log) }()
		}
	}

	<-ctx.Done()
	log.Info("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Minute)
	defer cancel()
	for _, s := range scalers {
		s.Shutdown(shutdownCtx)
	}
	wg.Wait()
	return nil
}

func systemInfo(id int) scaleset.SystemInfo {
	return scaleset.SystemInfo{System: "win-broker", Subsystem: "listener", Version: version, CommitSHA: "NA", ScaleSetID: id}
}

// serve registers (or reuses) the repository's scale set and listens until the context ends, retrying on errors.
func serve(ctx context.Context, cfg broker.Config, s *broker.Scaler, log *slog.Logger) {
	for ctx.Err() == nil {
		if err := listenOnce(ctx, cfg, s, log); err != nil && !errors.Is(err, context.Canceled) {
			log.Error("listener stopped; retrying", "error", err)
			select {
			case <-ctx.Done():
			case <-time.After(30 * time.Second):
			}
		}
	}
}

func listenOnce(ctx context.Context, cfg broker.Config, s *broker.Scaler, log *slog.Logger) error {
	client, err := scaleset.NewClientWithPersonalAccessToken(scaleset.NewClientWithPersonalAccessTokenConfig{
		GitHubConfigURL:     "https://github.com/" + s.Repo,
		PersonalAccessToken: cfg.Token,
		SystemInfo:          systemInfo(0),
	})
	if err != nil {
		return fmt.Errorf("scale set client: %w", err)
	}
	name := cfg.ScaleSetNameFor(s.Repo, s.Class)
	const defaultRunnerGroupID = 1
	set, err := client.GetRunnerScaleSet(ctx, defaultRunnerGroupID, name)
	if err != nil || set == nil {
		set, err = client.CreateRunnerScaleSet(ctx, &scaleset.RunnerScaleSet{
			Name:          name,
			RunnerGroupID: defaultRunnerGroupID,
			Labels:        []scaleset.Label{{Name: name}},
			RunnerSetting: scaleset.RunnerSetting{DisableUpdate: true},
		})
		if err != nil {
			return fmt.Errorf("register scale set %s: %w", name, err)
		}
		log.Info("registered the scale set", "name", name, "id", set.ID)
	} else {
		log.Info("reusing the scale set", "name", name, "id", set.ID)
	}
	client.SetSystemInfo(systemInfo(set.ID))
	s.ScaleSetID = set.ID
	s.Jit = client

	host, err := os.Hostname()
	if err != nil || host == "" {
		host = "win-broker"
	}
	session, err := client.MessageSessionClient(ctx, set.ID, host)
	if err != nil {
		return fmt.Errorf("message session: %w", err)
	}
	defer func() { _ = session.Close(context.WithoutCancel(ctx)) }()

	l, err := listener.New(session, listener.Config{ScaleSetID: set.ID, MaxRunners: cfg.MaxVMs, Logger: log.WithGroup("listener")})
	if err != nil {
		return fmt.Errorf("listener: %w", err)
	}
	log.Info("listening", "scaleSet", name)
	return l.Run(ctx, s)
}
