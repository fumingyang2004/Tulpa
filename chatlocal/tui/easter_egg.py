"""Hidden picture dialog opened by ↑↑↓↓←→←→baba (no visible menu entry).

textual-image probes the terminal (Sixel / TGP / cell size) when its modules are
imported, and that only works before Textual owns stdin, so the import happens
here at module level: app.py imports this module before the TUI starts.
"""
from pathlib import Path
import logging

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

class _QuietUnsupportedTerminal(logging.Filter):
    def filter(self, record):
        return not (record.getMessage() == 'Failed to probe terminal capabilities'
                    and record.exc_info and isinstance(record.exc_info[1], TimeoutError))


# An unanswered graphics probe is normal on plain terminals/SSH. The library
# already falls back to half-cell rendering; avoid a misleading startup stack.
_probe_log = logging.getLogger('textual_image._terminal')
_probe_filter = _QuietUnsupportedTerminal()
try:
    from PIL import Image as PILImage
except ImportError:  # Pillow missing: the dialog says so instead of crashing.
    PILImage = None

_probe_log.addFilter(_probe_filter)
try:
    from textual_image._terminal import get_cell_size
    from textual_image.renderable import Image as _AutoRenderable
    from textual_image.renderable.sixel import Image as _SixelRenderable
    from textual_image.renderable.tgp import Image as _TGPRenderable
    from textual_image.widget import HalfcellImage, Image as _GraphicsImage
    # Real pixels only on Sixel / TGP terminals; everything else (no TTY, plain
    # terminals) gets coloured half-cell characters rather than the unicode fallback.
    if _AutoRenderable in (_SixelRenderable, _TGPRenderable):
        ImageWidget = _GraphicsImage
    else:
        ImageWidget = HalfcellImage
    IMAGE_ERROR = ''
except Exception as exc:  # noqa: BLE001 - missing library or a failed terminal probe
    ImageWidget = None
    get_cell_size = None
    IMAGE_ERROR = f'无法加载 textual-image：{type(exc).__name__}'
finally:
    _probe_log.removeFilter(_probe_filter)

SEQUENCE = ('up', 'up', 'down', 'down', 'left', 'right', 'left', 'right', 'b', 'a', 'b', 'a')
ARROWS = 8  # After this many matched arrows, b/a belong to the sequence, not the page.
IMAGE_DIR = Path(__file__).resolve().parents[2] / 'web' / 'easter-egg'
IMAGES = ('1.png', '2.png')  # Left, right.
BACKGROUND = (15, 17, 23)  # #0f1117, the dialog background: transparency blends into it.
GAP = 2  # Columns between the two pictures.
CHROME_ROWS = 5  # Border (2) + title (1) + button (1) + spacing (1).
CHROME_COLS = 4  # Border (2) + padding (2).
_cache = {}


def load_picture(name):
    """Return (PIL image composited on the background, None) or (None, error text)."""
    if name in _cache:
        return _cache[name]
    if PILImage is None:
        return None, '未安装 Pillow'
    try:
        with PILImage.open(IMAGE_DIR / name) as source:
            source.load()
            rgba = source.convert('RGBA')
        flat = PILImage.new('RGBA', rgba.size, BACKGROUND + (255,))
        flat.alpha_composite(rgba)
        result = flat.convert('RGB'), None
    except Exception as exc:  # noqa: BLE001 - missing or broken file
        result = None, f'图片不可用：{name}（{type(exc).__name__}）'
    _cache[name] = result
    return result


def picture_cells(width, height, ratio):
    """Cell size (w, h) of each picture so both fit side by side at equal height."""
    cell = None
    if get_cell_size is not None:
        try:
            cell = get_cell_size()
        except Exception:  # noqa: BLE001
            cell = None
    cell_w, cell_h = (cell.width, cell.height) if cell and cell.width and cell.height else (10, 20)
    per_row = cell_h * ratio / cell_w  # Columns per row that keep the aspect ratio.
    rows_max = max(2, height - CHROME_ROWS)
    cols_max = max(4, (width - CHROME_COLS - GAP) // 2)
    rows = max(2, min(rows_max, int(cols_max / per_row)))
    cols = max(2, min(cols_max, round(rows * per_row)))
    return cols, rows


class SequenceTracker:
    """Matches SEQUENCE key by key; a wrong key keeps the longest overlapping prefix."""

    def __init__(self):
        self.progress = 0

    def reset(self):
        self.progress = 0

    def feed(self, key):
        """Advance with key; returns (consume, complete)."""
        before = self.progress
        typed = SEQUENCE[:before] + (key,)
        self.progress = next((n for n in range(min(len(typed), len(SEQUENCE)), 0, -1)
                              if typed[-n:] == SEQUENCE[:n]), 0)
        # b/a after the full arrow run belong to the sequence, never to the page.
        consume = before >= ARROWS and key in ('b', 'a')
        if self.progress == len(SEQUENCE):
            self.reset()
            return True, True
        return consume, False


def eligible(app):
    """Only on a normal page, never while typing into a text field or in a dialog."""
    return len(app.screen_stack) == 1 and not isinstance(app.focused, (Input, TextArea))


class EasterEggModal(ModalScreen):
    """1.png on the left, 2.png on the right, equal height, aspect ratio kept."""

    DEFAULT_CSS = """
    EasterEggModal { align: center middle; background: #0f1117 85%; }
    EasterEggModal > #egg-box { width: auto; height: auto; max-width: 100%; max-height: 100%;
        background: #0f1117; border: round #a78bfa; padding: 0 1; }
    EasterEggModal #egg-title { width: 100%; height: 1; content-align: center middle; color: #a78bfa;
        text-style: bold; }
    EasterEggModal #egg-pictures { width: auto; height: auto; }
    EasterEggModal .egg-picture { background: #0f1117; }
    EasterEggModal #egg-right { margin-left: 2; }
    EasterEggModal .egg-missing { width: 24; height: 5; color: #eab308; content-align: center middle; }
    EasterEggModal #egg-buttons { width: 100%; height: 1; align: center middle; margin-top: 1; }
    EasterEggModal #egg-close { min-width: 12; height: 1; border: none; }
    """
    BINDINGS = [Binding('escape', 'close', '关闭')]

    def __init__(self):
        super().__init__()
        self.pictures = []

    def compose(self) -> ComposeResult:
        with Vertical(id='egg-box'):
            yield Static('✦ 彩蛋 ✦', id='egg-title')
            with Horizontal(id='egg-pictures'):
                yield self.picture(IMAGES[0], 'egg-left')
                yield self.picture(IMAGES[1], 'egg-right')
            with Horizontal(id='egg-buttons'):
                yield Button('关闭 Esc', id='egg-close', variant='primary')

    def picture(self, name, widget_id):
        image, error = load_picture(name)
        if ImageWidget is None or image is None:
            return Static(error or IMAGE_ERROR, id=widget_id, classes='egg-missing')
        widget = ImageWidget(image, id=widget_id, classes='egg-picture',
                             on_error=lambda exc: Static(f'无法显示：{type(exc).__name__}', classes='egg-missing'))
        self.pictures.append(widget)
        return widget

    def on_unmount(self):
        # The library cleans up a rendered image when its image property is
        # cleared, but its base widget has no unmount cleanup of its own.
        for picture in self.pictures:
            picture.image = None
        self.pictures.clear()

    def on_mount(self):
        self.query_one('#egg-close').focus()
        self.fit()

    def on_resize(self, event: events.Resize):
        self.fit()

    def fit(self):
        """Give both pictures the same aspect-correct cell size for the current screen."""
        size = self.app.size
        widths, heights = [], []
        for name in IMAGES:
            image, _ = load_picture(name)
            if image is not None:
                widths.append(image.width)
                heights.append(image.height)
        if not widths:
            return
        ratio = max(w / h for w, h in zip(widths, heights))
        cols, rows = picture_cells(size.width, size.height, ratio)
        gap = GAP if size.width >= 2 * cols + GAP + CHROME_COLS else 1
        for picture in self.query('.egg-picture'):
            picture.styles.width, picture.styles.height = cols, rows
        self.query_one('#egg-right').styles.margin = (0, 0, 0, gap)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == 'egg-close':
            event.stop()
            self.action_close()

    def action_close(self):
        self.dismiss(None)
