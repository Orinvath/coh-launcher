# Source assets — skin `homecoming`

Owner-supplied artwork (Desktop/Launcher Assets, copied unchanged into
`pipeline/launcher-001/ref/owner-assets/`). Initial assets were copied without AI editing; later replacements are recorded below. Hashes are SHA256.

| Skin file | Source basename | Source SHA256 | Transformation |
|---|---|---|---|
| assets/background.png | 7f50a7c0-e656-4d9b-812b-19c3ee712be1.png | dc6553f8c491241a39a3b384a527eeca6642fe3f96f9663e976539ae480c9ed8 | Copied unchanged (1672×941 RGB) |
| assets/feature1.png | 73c4a8e8-9e0a-4647-881a-77246e0b8676.png | 3921d4785d16490af60b85b2e7da2b4c0e13cbc3a4c07cffec87ae13fad0d693 | Copied unchanged (1448×1086 RGB) |
| assets/feature2.png | ca7c35ce-0903-47f7-8332-3fceb454d377.png | fc9cd677bb8b904fe862d3b4e8151a1387a4996bed814ef3d1a414cdf246873a | Copied unchanged (1448×1086 RGB) |
| assets/feature3.png | 69569f55-9bcc-4f90-9eac-8c30c59442e2.png | c384c44ecdeb6687644ebd27236dd6e0806890c3ad60ea766f082724fabe71e8 | Copied unchanged (1448×1086 RGB) |
| assets/feature4.png | ab0ca47a-ec22-4ff9-aec1-115173e859bd.png | 386c97b98da699dfff49abcdef51dba771e45249a4d7827cf18f337cae3ee5e8 | Copied unchanged (1448×1086 RGB) |
| assets/logo.png | city_of_heroes_logo_transparent.png | c9e9d11e6bef5619a4ff1c38d71ffa9567d47b6ed5804822b610cfd73be6a0d2 | Re-encoded as PNG (Pillow, optimize). Alpha bounding box equals the full 995×745 canvas, so no pixels were cropped. Output SHA256 eec0963e1b332c23526fb83ab5d22b290df92286ff3ccccf646364f31346696e |
| assets/brand.png | homecoming_skyline_transparent.png | b4f8569ad224ea9411806d9d7efd80a99349ba4de93a37dc6cad32ec325ebdd3 | Re-encoded as PNG (Pillow, optimize). Alpha bounding box equals the full 987×770 canvas, so no pixels were cropped. Output SHA256 a518adc9cd99d85d4309bff532a75a843883705727c75eaea5ea58e0bf0291cc. **Superseded by the owner replacement below.** |
| assets/brand.png (current) | Futuristic Blue City Homecoming Emblem.png (owner supplied, Desktop/Launcher Assets) | 3a682b195d4e506fccbaf8b607e9290c9f5b572fa46ceb75936195cee780cac4 | Copied byte-for-byte by Planner as delivery asset operation; no image edits. |

Owner mapping (design-spec.md, "Approved source artwork"): background = rooftop
hero; feature1 = city/blimp; feature2 = molten enemy battle; feature3 = alley
team; feature4 = electric hero.

## Fonts (not in the skin; fixed under ui/fonts)

| File | Source | Licence |
|---|---|---|
| ui/fonts/InstrumentSans-Variable.ttf | ~/.local/share/fonts/InstrumentSans/InstrumentSans[wdth,wght].ttf (installed) | SIL OFL 1.1, text in ui/fonts/InstrumentSans-OFL.txt |
| ui/fonts/NotoSans-ExtraCondensed{Light,Medium,Bold,ExtraBold}.ttf | /usr/share/fonts/google-noto/ (installed) | SIL OFL 1.1, text in ui/fonts/NotoSans-OFL.txt |

## Approved Halloween artwork — 2026-10-31

`assets/feature1.png` now contains the owner-approved Statesman versus Eochai cinematic image, copied unchanged from `../3D Cinematic Artwork/output/reference-match/reference.png` (1672×941). This supersedes the city/blimp artwork in the existing event image slot, used by the Halloween card and carousel. It was generated using actual game asset renders as references.

SHA256: `be90fc435c2864ef6e2483668fa20ca7bf5dd67b00e880599570b37ddafe325e`. Local backup and ownership-aware rollback record: `var/artwork-backups/halloween-20261031/manifest.json`.
