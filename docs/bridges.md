# Bridge notes

## No mautrix fork or patch is required

The thumbnail module works with ordinary Matrix events already produced by mautrix bridges. It does not log in to WhatsApp, Messenger, Instagram, Google Messages, or any other remote service, and it needs no bridge tokens.

Keep bridges on a supported release and follow their own upgrade guides. A bridge upgrade may be useful for unrelated fixes, but it is not a prerequisite for this Synapse module.

## Optional mautrix-whatsapp-native thumbnails

Some mautrix-whatsapp versions support:

```yaml
network:
  whatsapp_thumbnail: true
```

This asks the WhatsApp bridge path to provide its own thumbnail, which can be lower resolution than a locally extracted poster frame. It is WhatsApp-specific and does not add thumbnails to Meta, Signal, Google Messages, or other bridges.

Check the example configuration and release notes for your exact mautrix-whatsapp version before adding the option. Merge it under the existing `network:` mapping rather than replacing that mapping, then restart only the WhatsApp bridge. If the option is unsupported or already provides what you need, do not enable the Synapse module as well.

The Synapse module skips events that already contain `thumbnail_url` or `thumbnail_file`, so it does not intentionally replace a bridge-provided thumbnail.

## Double puppeting

With `only_appservice_users: true`, the module accepts the appservice bot and users matched by its exclusive namespaces. A double-puppeted message can instead be sent by the user's ordinary local Matrix account and therefore be skipped. Leave the option false if those messages also need processing, understanding that all eligible local video sends can then receive thumbnails.

## Encrypted bridge rooms

A Synapse module cannot decrypt an encrypted event or its encrypted attachment. This repository deliberately passes `m.room.encrypted` events through untouched.

Supporting encrypted rooms requires the bridge to generate and attach a thumbnail before it encrypts the event and media. That must be implemented in each bridge's sending path; installing ffmpeg in Synapse cannot provide it. Do not weaken room encryption as a workaround.
