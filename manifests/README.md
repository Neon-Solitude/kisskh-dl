# manifests/

Drop manifest JSON files here. Everything in this folder **except this README is
gitignored**, so your collected manifests are never committed.

## How files get here

The [browser collector](../tools/browser_collector.js) has a **💾 Download**
button. Clicking it saves `<drama>_manifest.json` to your browser's Downloads
folder — move that file into this directory.

(A browser console script can't write directly to disk, so the download +
manual move is the simplest reliable path.)

## Then download

```console
kissget dl --from-manifest manifests/<drama>_manifest.json -s en -o "C:\Users\you\Videos"
```
