# Synapse compatibility

## Required minimum: Synapse 1.154.0

This module registers Synapse's `check_event_allowed` third-party-rules callback. On affected older Synapse releases, registering that callback causes the event to be frozen before validation. An `m.mentions` value then becomes an `immutabledict` (and its arrays can become tuples), which the older Pydantic mention validator did not accept.

The visible failure was not limited to videos: ordinary messages could be rejected with HTTP 400 / `M_BAD_JSON`, including text sent by Element Web and messages sent through bridges. A typical error contained:

```text
Input should be a valid dictionary or instance of Mentions
input_value=immutabledict({})
```

Synapse fixed this in **1.154.0** through [element-hq/synapse#19634](https://github.com/element-hq/synapse/pull/19634), tracked in [element-hq/synapse#19689](https://github.com/element-hq/synapse/issues/19689) and listed in the [1.154.0 release notes](https://github.com/element-hq/synapse/releases/tag/v1.154.0).

For that reason:

- Do not install this module on Synapse 1.153.x or earlier.
- Do not treat disabling `m.mentions` in one client as a server-side fix.
- Do not leave the module enabled while troubleshooting unrelated delivery failures.
- After future Synapse upgrades, repeat the disposable integration test before production rollout.

The included example pins **1.165.0**, the version used for the Parallax deployment and compatibility check. This is a tested pin, not a claim that every later Synapse release is automatically compatible.

## Internal API boundary

The public `ModuleApi` callback is used for event replacement, but uploading the generated JPEG requires Synapse's internal media repository. The module therefore accesses `api._hs`, the main datastore, and `MediaRepository.create_or_update_content`. Those internals can change without the same compatibility guarantees as the public module API.

The module also preserves the event dictionary through a JSON round trip so frozen mappings become mutable ordinary dictionaries. Synapse rebuilds the event from the replacement before persistence, retaining the appservice timestamp and transaction semantics covered by the integration test.

## Upgrade and rollback rule

Back up the database before changing Synapse versions. Database migrations can make an older Synapse image incompatible with the current database even when the module itself is removed. A safe rollback plan includes both configuration and a database restore procedure tested by the administrator.
