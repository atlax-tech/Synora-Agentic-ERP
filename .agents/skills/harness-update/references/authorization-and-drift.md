# Authorization and drift protocol

## Drift categories

- `source-modified`, `source-added`, `source-missing`;
- `managed-content-modified`, `managed-content-missing`;
- `implementation-structure`, `command`, `test`, or `ci` change established by
  inspected evidence;
- `manifest-invalid` or `baseline-missing`;
- semantic conflict established by reading both sources.

Raw SHA-256 is byte-sensitive. A line-ending-only change is still fingerprint
drift; the proposal may recommend normalization but may not silently ignore it.

## Immutable proposal

Give each proposal a digest or stable ordered item list. Approval expires if any
input hash changes. A proposal includes no writes—not even a "last checked"
timestamp.

## Managed section markers

```html
<!-- harness-armor:managed id="knowledge-map" -->
managed content
<!-- /harness-armor:managed -->
```

Never edit outside the marker pair. Missing, duplicated, or nested markers are a
conflict requiring read-only diagnosis.

