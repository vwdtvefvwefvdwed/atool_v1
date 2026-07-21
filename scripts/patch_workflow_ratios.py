import pathlib

base = pathlib.Path("backend/workflows")
changed = []

def patch(name, old, new, count=1, required=True):
    p = base / name / "workflow.py"
    src = p.read_text(encoding="utf-8")
    n = src.count(old)
    if n == 0:
        if required:
            print(f"!! MISS {name}: pattern not found: {old[:60]}")
        return
    src = src.replace(old, new, count)
    p.write_text(src, encoding="utf-8", newline="\n")
    changed.append(f"{name} (x{min(n, count)})")

# 12 workflows whose image_edit hardcodes 1:1 (first occurrence = image_edit;
# the later upscale-step 1:1 stays — upscalers ignore ratio).
for wf in ["knight_style_img_to_img", "got_style_img_to_img", "avatar_style_img_to_img",
           "angled_lookback_shot", "motion_caught_portrait", "pencil_physique_portrait",
           "crosshatch_girl_study", "shadow_contrast_profile", "veiled_top_angle_portrait",
           "csk_roar_2k26", "mi_gangsters", "rcb_king_kohli"]:
    patch(wf, "'aspect_ratio': '1:1',", "'aspect_ratio': self.requested_aspect_ratio or '1:1',", 1)

# FIFA poster: default stays 9:16 unless the user picked a ratio.
patch("fifa_world_cup", "'aspect_ratio': '9:16',", "'aspect_ratio': self.requested_aspect_ratio or '9:16',", 1)

# Speed-ramp reel poses: default 9:16 (vertical reel), user-overridable.
patch("speed_ramp_edit",
      "'aspect_ratio': '9:16',             # vertical, matches reel output",
      "'aspect_ratio': self.requested_aspect_ratio or '9:16',  # default vertical (reel output)", 1)

# Anime edit: poses AND motion clips follow the same requested ratio.
patch("anime_edit", "aspect_ratio='9:16',", "aspect_ratio=self.requested_aspect_ratio or '9:16',", 2)

# Common workflow: also honour the base-class attribute (kept reading the
# upload-step output first for backward compat with saved checkpoints).
patch("common_workflow",
      "aspect_ratio = input_data.get('aspect_ratio') or '1:1'",
      "aspect_ratio = input_data.get('aspect_ratio') or self.requested_aspect_ratio or '1:1'", 1)

print("PATCHED:", ", ".join(changed))
