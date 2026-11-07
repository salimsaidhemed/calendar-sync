# Delivery roadmap

GitHub issues and milestones are the delivery tracker. Dates remain unset until scope and capacity are agreed. M1–M3 deliver v0.1.0; M4–M5 are future work. The initial worker syncs Google to Nextcloud; bidirectional sync needs the policy work in M5.

## M1 — Repository foundation

Epic [#1](https://github.com/salimsaidhemed/calendar-sync/issues/1).

- [#6](https://github.com/salimsaidhemed/calendar-sync/issues/6) Publish the scaffold through a feature PR and verify CI
- [#7](https://github.com/salimsaidhemed/calendar-sync/issues/7) Document contribution workflow and change history
- [#8](https://github.com/salimsaidhemed/calendar-sync/issues/8) Verify main protection and required CI checks

## M2 — Safe calendar sync engine

Epic [#2](https://github.com/salimsaidhemed/calendar-sync/issues/2).

- [#10](https://github.com/salimsaidhemed/calendar-sync/issues/10) Implement configuration-scoped SQLite state and migrations
- [#9](https://github.com/salimsaidhemed/calendar-sync/issues/9) Implement Google OAuth and complete calendar reads
- [#11](https://github.com/salimsaidhemed/calendar-sync/issues/11) Implement Nextcloud CalDAV adapter and event normalization
- [#12](https://github.com/salimsaidhemed/calendar-sync/issues/12) Implement deterministic dry-run planner and ownership checks
- [#13](https://github.com/salimsaidhemed/calendar-sync/issues/13) Implement conditional execution and crash recovery

## M3 — v0.1.0 release readiness

Epic [#3](https://github.com/salimsaidhemed/calendar-sync/issues/3).

- [#15](https://github.com/salimsaidhemed/calendar-sync/issues/15) Implement continuous worker lifecycle and health reporting
- [#16](https://github.com/salimsaidhemed/calendar-sync/issues/16) Write OAuth, Nextcloud and recovery setup guide
- [#14](https://github.com/salimsaidhemed/calendar-sync/issues/14) Run safety acceptance suite and prepare v0.1.0 release PR

## M4 — Multi-user UI and SSO (future)

Epic [#4](https://github.com/salimsaidhemed/calendar-sync/issues/4).

- [#17](https://github.com/salimsaidhemed/calendar-sync/issues/17) Design API boundaries and per-user authorization
- [#19](https://github.com/salimsaidhemed/calendar-sync/issues/19) Implement Keycloak login and configuration UI
- [#18](https://github.com/salimsaidhemed/calendar-sync/issues/18) Expose audit history and sync status UI

## M5 — Advanced sync (future)

Epic [#5](https://github.com/salimsaidhemed/calendar-sync/issues/5).

- [#21](https://github.com/salimsaidhemed/calendar-sync/issues/21) Specify recurrence and exception interoperability
- [#20](https://github.com/salimsaidhemed/calendar-sync/issues/20) Specify bidirectional conflicts and deletion safeguards

## Definition of done

Each change has a linked issue, feature branch, updated documentation and changelog, verification evidence, and an owner-approved PR. Only the owner merges. Epic completion requires all child issues to be verified; creating the scaffold does not complete the release.
