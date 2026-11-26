# Drawing the views

Every view is built by a throwaway generator that uses the kit `drawio_kit.py` (in `$T`), so all views share one visual language. Copy the structure of `assets/example/generators/view02.py`: constants at the top (`OWNERS`, `STATE`), a `build() -> Diagram` function, a `main()` that writes the `.drawio` file given as argument.

## The visual language

- **Box colour = who runs it**, from the kit's `ROLES`: `hebergeur` (blue: network, firewall, VMs, host services), `agence` (green: containers, configuration, deployment), `client` (orange: the client's systems and users), `tiers` (purple: third-party services), `internet` (white: users, the Internet), `perso` (red: personal data, through the `extra` box of the legend). `confirm=True` draws a box grey and dashed: it is « à confirmer ».
- **Frames** (`zone`) are network zones and machines, nested: host, private network, VM. Boxes inside a VM are its containers or services. A zone takes the role of who runs it.
- **Arrow colour = nature of the flow**, from `NATURES`: `visiteur` (black: visitor traffic), `donnees` (blue: data flows), `admin` (red: administration, deployment, monitoring), `confirm` (grey: flow to confirm). `clear=True` draws it dashed: unencrypted, and its label ends with « (clair) ». Orthogonal arrows cross with an arc (`jumpStyle=arc`), set by the kit.
- **Pictograms** come from the draw.io built-in libraries, listed in `ICONS` (`users`, `internet`, `network`, `vm`, `docker`, `database`, `ssh`, `firewall`, `monitoring`, `api`, `gitlab`, `registry`...). They are part of the box style, so they follow the box. An unknown name raises an error: pick the closest existing one.
- **Labels** are plain text (`html=0`, set by the kit). Write line breaks with `\n`; free text blocks (`text`) wrap to their width by themselves.
- **Flow labels** are short: `P06 · 3306/tcp`, plus « (clair) » when unencrypted, the ID first. On the overview, where ports would clutter, use a function then the IDs: « consultation (P01) ». Arrows without an ID are exchanges inside one machine (Docker network); they need no matrix row.
- **SSH access**: draw one `sshd :22` box (icon `ssh`) on every VM, reached through the host's NAT with its public port on the arrow (« P06 · 22022/tcp » to the NAT, « P06 · 22/tcp » to the VM).
- **Side containers** (telemetry, log or backup agents) are drawn as containers in their own right, with their outgoing flow.
- **Secondary systems** (forge, code analysis, registry) stay compact, in one zone per owner.
- **Every view** has the legend (`legend(x, y, OWNERS)`, which never shows a flow ID) and the cartouche (`cartouche(...)`: title, environment, `État au JJ/MM/AAAA`, sources). Keep both at the same place on every view (bottom left and bottom right in the example).
- **Comparable views**: the preproduction view keeps the layout of the production one, so the differences jump out.

## Geometry

Coordinates are relative to the parent zone. Work on a grid: columns for actors, the host, third parties; rows aligned so that most arrows are straight. Give `edge()` the exit and entry points as fractions of the boxes (`(1, 0.5)` = middle of the right side) and waypoints (`points`) in absolute page coordinates when the route must go around something. The label sits on the longest segment unless `label_seg` and `label_frac` place it elsewhere.

## Render, read, fix

For each view:

1. `PYTHONPATH="$T" python3 build/gen/NN-<slug>.py build/gen/NN-<slug>.drawio` and check that `build().overlaps()` is empty (sibling boxes that intersect).
2. `python3 "$T/architecture.py" render build/gen/NN-<slug>.drawio views/NN-<slug>.drawio.svg --png build/NN-<slug>.png`.
3. Read the PNG. Look for labels over boxes or over other labels, arrows through boxes, text that overflows its box, empty or cramped areas. Fix the generator and render again until it reads cleanly.
4. Give the user the PNG path, take their comments, iterate.

Once the user validates a view, the `.drawio.svg` is the source. To change it later: `python3 docs/architecture/tools/architecture.py extract views/NN-<slug>.drawio.svg build/NN-<slug>.drawio`, edit the XML, then `render` it back to the same view and read the PNG again. The user can also edit it in draw.io; then run `make diagrams`.
