"""从共同设计稿导出三个独立预览和待选择的设计变量，不调用后端。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "design/previews/index.html").read_text(encoding="utf-8")
definitions = [
    ("clear", "A 清爽制作台", "蓝白双栏，流程侧栏便于查看制作过程",
     ["#2759d8", "#596b83", "#f3f6fb", "#ffffff", "#17253b"], "Noto Sans SC", "16px",
     "流程侧栏 + 材料／成品双栏"),
    ("studio", "B 深色录音室", "深蓝灰与柔和琥珀色，突出配音和收听",
     ["#d9a46b", "#b4bfce", "#171d29", "#202837", "#edf0f5"], "IBM Plex Sans", "9px",
     "窄流程栏 + 开放编辑区 + 音频面板"),
    ("editorial", "C 文稿编辑室", "淡紫灰和衬线标题，强调阅读与内容打磨",
     ["#7052a3", "#706780", "#f7f6fb", "#ffffff", "#332c47"], "Noto Serif SC", "8px",
     "居中标题 + 文稿编辑面板 + 无边框脚本区"),
]
styles = []
for key, name, description, colors, font, radius, layout in definitions:
    preview = source.replace(
        'data-theme="clear" class="clear"', f'data-theme="{key}" class="{key}"',
    ).replace("|| 'clear';", f"|| '{key}';")
    (ROOT / f"design/previews/{key}.html").write_text(preview, encoding="utf-8")
    styles.append({
        "id": key, "name": name, "description": description, "preview_html": preview,
        "design_system": {
            "colors": dict(zip(
                ("primary", "secondary", "background", "surface", "text"), colors, strict=True,
            )) | {"accent": colors[0]},
            "typography": {
                "heading_font": font, "body_font": "Noto Sans SC",
                "google_fonts_url": "https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700",
            },
            "border_radius": radius, "shadow_style": "minimal",
            "layout_pattern": layout,
            "effects": "180ms color transitions; reduced motion respected",
            "anti_patterns": [
                "不使用预制音频冒充真实调用", "不添加尚未支持的历史节目和账号功能",
                "设计未确认前不对接 API",
            ],
        },
    })
target = ROOT / ".cc-design/variants.json"
target.parent.mkdir(exist_ok=True)
selected_file = target.parent / "design-system.json"
selection = json.loads(selected_file.read_text(encoding="utf-8")) if selected_file.exists() else {}
target.write_text(json.dumps({
    "version": "1.0", "status": "style_selected" if selection else "awaiting_selection",
    "selected_id": selection.get("selected_id"),
    "project": "文本转双人播客工作台", "styles": styles,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print("Saved 3 standalone previews; " + (
    f"selected style: {selection['selected_id']}." if selection else "awaiting style selection."
))
