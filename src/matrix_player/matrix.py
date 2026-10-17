import threading
from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple

from PIL import Image


@dataclass(frozen=True)
class MatrixConfig:
    rows: int = 32
    cols: int = 64
    chain_length: int = 1
    parallel: int = 3
    multiplexing: int = 1
    pixel_mapper_config: str = "Rotate:90"
    pwm_lsb_nanoseconds: int = 160
    gpio_slowdown: int = 2

    @property
    def size(self) -> Tuple[int, int]:
        return self.cols * self.chain_length, self.rows * self.parallel

    @property
    def logical_size(self) -> Tuple[int, int]:
        """Display orientation after the configured pixel mapper is applied."""
        return self.rows * self.parallel, self.cols * self.chain_length


class MatrixAdapter(Protocol):
    @property
    def size(self) -> Tuple[int, int]:
        ...

    @property
    def frame(self) -> Image.Image:
        """Copy of the image currently on the panel."""
        ...

    def show(self, image: Image.Image) -> None:
        ...

    def blank(self) -> None:
        ...

    def close(self) -> None:
        ...


class MockMatrixAdapter:
    """Thread-safe in-memory adapter suitable for tests and local preview."""

    def __init__(self, size: Tuple[int, int] = (96, 64), max_history: int = 32) -> None:
        if max_history <= 0:
            raise ValueError("max_history must be positive")
        self._size = size
        self.max_history = max_history
        self._lock = threading.RLock()
        self._frame = Image.new("RGB", size)
        self._history: List[Image.Image] = []
        self.closed = False

    @property
    def size(self) -> Tuple[int, int]:
        return self._size

    @property
    def frame(self) -> Image.Image:
        with self._lock:
            return self._frame.copy()

    @property
    def history(self) -> List[Image.Image]:
        with self._lock:
            return [frame.copy() for frame in self._history]

    def show(self, image: Image.Image) -> None:
        frame = image.convert("RGB")
        if frame.size != self.size:
            raise ValueError("frame size {} does not match matrix {}".format(frame.size, self.size))
        with self._lock:
            if self.closed:
                raise RuntimeError("matrix is closed")
            self._frame = frame.copy()
            self._history.append(self._frame.copy())
            del self._history[:-self.max_history]

    def blank(self) -> None:
        self.show(Image.new("RGB", self.size))

    def close(self) -> None:
        with self._lock:
            if not self.closed:
                self._frame = Image.new("RGB", self.size)
                self._history.append(self._frame.copy())
                del self._history[:-self.max_history]
                self.closed = True


class RGBMatrixAdapter:
    """Lazy hardware adapter; importing this module never requires rgbmatrix."""

    def __init__(self, config: Optional[MatrixConfig] = None) -> None:
        selected = config or MatrixConfig()
        try:
            from rgbmatrix import RGBMatrix, RGBMatrixOptions
        except ImportError as error:
            raise RuntimeError(
                "rpi-rgb-led-matrix is required for the hardware adapter"
            ) from error
        options = RGBMatrixOptions()
        options.rows = selected.rows
        options.cols = selected.cols
        options.chain_length = selected.chain_length
        options.parallel = selected.parallel
        options.multiplexing = selected.multiplexing
        options.pixel_mapper_config = selected.pixel_mapper_config
        options.pwm_lsb_nanoseconds = selected.pwm_lsb_nanoseconds
        options.gpio_slowdown = selected.gpio_slowdown
        # The library drops to the 'daemon' user after init unless told otherwise,
        # which would strip the service's access to its media and state directories.
        options.drop_privileges = False
        self.config = selected
        self._matrix = RGBMatrix(options=options)
        self._size = (self._matrix.width, self._matrix.height)
        # Draw off-screen and swap on vsync so motion never tears mid-refresh.
        self._canvas = self._matrix.CreateFrameCanvas()
        self._frame = Image.new("RGB", self._size)
        self._lock = threading.RLock()

    @property
    def size(self) -> Tuple[int, int]:
        """Canvas size reported by the driver once pixel mappers are applied."""
        return self._size

    @property
    def frame(self) -> Image.Image:
        with self._lock:
            return self._frame.copy()

    def show(self, image: Image.Image) -> None:
        if image.size != self.size:
            raise ValueError("frame size {} does not match matrix {}".format(image.size, self.size))
        frame = image.convert("RGB")
        with self._lock:
            # The Rotate:90 pixel mapper already presents the driver's 64x96
            # framebuffer as a 96x64 canvas, so frames are sent through as-is.
            self._canvas.SetImage(frame)
            self._canvas = self._matrix.SwapOnVSync(self._canvas)
            self._frame = frame

    def blank(self) -> None:
        with self._lock:
            self._canvas.Clear()
            self._canvas = self._matrix.SwapOnVSync(self._canvas)
            self._frame = Image.new("RGB", self._size)

    def close(self) -> None:
        self.blank()


MockMatrix = MockMatrixAdapter
HardwareMatrix = RGBMatrixAdapter
