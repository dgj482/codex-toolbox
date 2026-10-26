"""Provider card CSS is display data, never an application/provider configuration.

The UI validates CSS declarations and applies only approved decorative
properties to explicitly named card elements. Never insert this text in a style
element: stored data is untrusted, including data from older versions.
"""

EXAMPLE_CSS = """.provider-card {
  background: linear-gradient(135deg, #f6f8ff, #edeaff);
  border-color: #c8ccfa;
  border-radius: 16px;
  box-shadow: 0 6px 18px rgba(86, 94, 180, 0.12);
}
.provider-icon { background: #e4e7ff; color: #4658bc; }
.provider-name { color: #27345c; font-weight: 700; }
.provider-detail { color: #657395; }
"""


def stored_css(value):
    if not isinstance(value, str):
        raise ValueError("CSS 样式必须是文本")
    if len(value.encode("utf-8")) > 32768:
        raise ValueError("CSS 文件不能超过 32 KB")
    return value.strip()
