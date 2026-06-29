# Simulation camera screenshots

These JPEGs are real Frigate stills captured from the maintainer's home
cameras and used as backgrounds for Simulation Mode so the design
review experience is visually 1:1 with the real running app.

## Privacy note

If you fork or clone this repo:

- These are **real photos of the maintainer's home**. Replace them with
  your own placeholder images (or generated synthetic ones) before
  publishing your fork publicly.
- The Simulation Mode camera renderer (`../simulation-cameras.jsx`)
  falls back to abstract SVG placeholders when these JPEGs aren't
  present, so it's safe to delete this folder if you don't want them.

## Refreshing the screenshots

If you have Frigate running locally, dump fresh stills like this
(from the bridge container, since it has HA access):

```bash
ssh hav-ubuntu "sudo docker exec hav-personaplex-bridge python -c '
import urllib.request
for cam in [\"living_room\",\"kitchen\",\"dining_room\",\"workshop\",\"driveway\"]:
  with open(f\"/tmp/sim_{cam}.jpg\",\"wb\") as f:
    f.write(urllib.request.urlopen(f\"http://192.168.0.125:5000/api/{cam}/latest.jpg\",timeout=10).read())
'"
# then docker cp out + scp to your machine
```
