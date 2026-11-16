# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      drivers/audio_sfx.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Non-blocking, spam-proof DMA I2S acoustic engine, formant WAV
#              vocalizer ("MEEP!"), and psychoacoustic log-taper volume driver
#              for Slot 6 TAS2780 25W Class-D Audio Amplifier.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import gc
import math
import struct
import time
from machine import I2S


class _PlayerBridge:
    # Replaces Pimoroni's WavPlayer on AudioAmpModule so enable/disable/reset
    # never invoke WavPlayer's blocking spinlock or duplicate I2S(0) instances.
    # Defines both unmangled (__stop_i2s, used by MicroPython) and CPython-mangled names.
    def __init__(self, owner):
        self._owner = owner

    def __start_i2s(self):
        pass

    def __stop_i2s(self):
        self._owner._teardown_i2s()

    def _AudioAmpModule__start_i2s(self):
        pass

    def _AudioAmpModule__stop_i2s(self):
        self._owner._teardown_i2s()


class ConeAudio:
    # Stream modes (atomic integer transitions read by I2S DMA callback)
    _MODE_IDLE = 0
    _MODE_WAV = 1
    _MODE_TONE = 2

    # Waveform shape identifiers
    SINE = 0
    SQUARE = 1
    TRI = 2
    BRASS = (0, 1)
    REED = (2, 1)

    SAMPLE_RATE = 44100
    WAV_CHUNK_BYTES = 512
    I2S_IBUF_BYTES = 3072
    IDLE_SHUTDOWN_MS = 15000

    # TAS2780 Page 0 registers & pop-free power-down modes
    _REG_PAGE = 0x00
    _REG_MODE_CTRL = 0x02
    _REG_DVC = 0x1A
    _DVC_MUTE = 0xCF
    _MODE_ACTIVE = 0x80
    _MODE_ACTIVE_MUTED = 0x81
    _MODE_SW_SHUTDOWN = 0x02
    SOFT_RAMP_STEP_MS = 50

    # Perceptual loudness ceiling (-4.0 dB protects 3" 4-Ohm 5W transducer on 3S LiPo)
    MAX_GAIN_DB = -4.0
    MIN_GAIN_DB = -54.0

    WAV_FILES = {
        "meep": ("meep.wav", 1),
        "meep_meep": ("meep.wav", 2),
    }

    PATTERNS = {
        "meep": (
            (390, 35, TRI),
            (540, 130, REED),
            (460, 20, TRI),
        ),
        "meep_meep": (
            (390, 35, TRI),
            (540, 125, REED),
            (0, 55, SINE),
            (410, 35, TRI),
            (565, 135, REED),
        ),
        "chime": (
            (523, 110, SINE),
            (659, 110, SINE),
            (784, 110, SINE),
            (1047, 240, SINE),
        ),
        "horn": (
            (330, 180, BRASS),
            (0, 55, SINE),
            (392, 340, BRASS),
        ),
        "siren": (
            (660, 220, TRI),
            (880, 220, TRI),
            (660, 220, TRI),
            (880, 220, TRI),
            (660, 220, TRI),
            (880, 220, TRI),
        ),
        "chirp": (
            (880, 65, SINE),
            (1319, 65, SINE),
            (1760, 80, SINE),
            (0, 35, SINE),
            (1175, 70, SINE),
            (1568, 140, SINE),
        ),
        "beep": (
            (1000, 260, SQUARE),
            (0, 220, SINE),
            (1000, 260, SQUARE),
            (0, 220, SINE),
            (1000, 260, SQUARE),
        ),
        "alarm": (
            (1400, 90, SQUARE),
            (950, 90, SQUARE),
            (1400, 90, SQUARE),
            (950, 90, SQUARE),
        ),
    }

    def __init__(self, audio_mod, yukon, default_volume=0.35, i2s_id=0):
        self.amp = audio_mod
        self.yukon = yukon
        self.i2s_id = i2s_id

        # Extract public Slot pins from AudioAmpModule.slot and replace buggy WavPlayer
        slot = audio_mod.slot
        self._pin_sd = slot.FAST1
        self._pin_sck = slot.FAST2
        self._pin_ws = slot.FAST3
        self._pin_en = slot.SLOW1
        self.amp.player = _PlayerBridge(self)
        gc.collect()

        # Pre-allocated ping-pong DMA buffers shared by WAV & Tone modes
        # (zero heap churn during IRQ streaming; never mutates in-flight DMA buffer)
        self._silence = bytearray(self.WAV_CHUNK_BYTES)
        self._dma_bufs = (
            bytearray(self.WAV_CHUNK_BYTES),
            bytearray(self.WAV_CHUNK_BYTES),
        )
        self._dma_mvs = (
            memoryview(self._dma_bufs[0]),
            memoryview(self._dma_bufs[1]),
        )
        self._dma_active_idx = 0
        self._dma_inflight_idx = 0
        self._tone_buf = None

        self._stream_mode = self._MODE_IDLE
        self._amp_configured = False
        self._shutdown_stage = 0
        self._shutdown_step_ms = 0

        # Cached WAV handle for O(1) instant re-triggering on rapid button taps
        self._wav_f = None
        self._wav_open_name = None
        self._wav_data_offset = 44
        self._wav_data_size = 0
        self._wav_rem = 0

        self.volume = max(0.0, min(1.0, float(default_volume)))
        self.gain_db = self._calc_gain_db(self.volume)
        self.active_sound = None
        self._play_kind = None
        self._seq = ()
        self._idx = 0
        self._wav_repeats_left = 0
        self._wav_gap_active = False
        self._step_end_ms = 0
        self._last_active_ms = 0
        self._busy = False

        # Allocate I2S(0) ONCE at boot while heap is unfragmented and keep its
        # 44.1 kHz clock running with silence. Never call I2S.deinit() at runtime:
        # on Yukon Wireless, RP2040 machine.I2S.deinit() disables shared DMA_IRQ_0
        # globally and kills CYW43 PIO SPI Wi-Fi.
        gc.collect()
        self._i2s = I2S(
            self.i2s_id,
            sck=self._pin_sck,
            ws=self._pin_ws,
            sd=self._pin_sd,
            mode=I2S.TX,
            bits=16,
            format=I2S.MONO,
            rate=self.SAMPLE_RATE,
            ibuf=self.I2S_IBUF_BYTES,
        )
        self._i2s.irq(self._i2s_irq)
        self._i2s.write(self._silence)

    @classmethod
    def _calc_gain_db(cls, volume):
        v = max(0.0, min(1.0, float(volume)))
        if v <= 0.005:
            return None  # Hard digital mute
        # Stevens' Power Law perceptual taper (+10 dB = 2x perceived loudness)
        # Makes 50% sound half as loud as 100%, waking low end & taming top end
        db = cls.MAX_GAIN_DB + 33.22 * math.log10(v)
        if db < cls.MIN_GAIN_DB:
            db = cls.MIN_GAIN_DB
        elif db > cls.MAX_GAIN_DB:
            db = cls.MAX_GAIN_DB
        return round(db, 1)

    @classmethod
    def _volume_to_dvc_byte(cls, volume):
        db = cls._calc_gain_db(volume)
        if db is None:
            return 0xCF  # TAS2780 digital mute (> -100 dB)
        # TAS2780 DVC register 0x1A: 0x00 = 0 dB, +1 LSB = -0.5 dB attenuation
        att_steps = int(round(-db * 2.0))
        return max(0, min(0xC8, att_steps))

    def _apply_dvc_volume(self):
        self.gain_db = self._calc_gain_db(self.volume)
        if self.amp.is_enabled():
            dvc = self._volume_to_dvc_byte(self.volume)
            try:
                self.amp.write_i2c_reg(self._REG_PAGE, 0x00)
                self.amp.write_i2c_reg(self._REG_DVC, dvc)
            except Exception:
                pass

    def _i2s_irq(self, arg):
        i2s = self._i2s
        if i2s is None:
            return
        try:
            mode = self._stream_mode
            if mode == 1:  # _MODE_WAV
                f = self._wav_f
                rem = self._wav_rem
                if f is not None and rem > 0:
                    ping = self._dma_inflight_idx ^ 1
                    self._dma_inflight_idx = ping
                    self._dma_active_idx = ping
                    mv = self._dma_mvs[ping]
                    chunk_sz = self.WAV_CHUNK_BYTES
                    if rem >= chunk_sz:
                        n = f.readinto(mv)
                    else:
                        n = f.readinto(mv[:rem])
                    if n and n > 0:
                        self._wav_rem = rem - n
                        if n < chunk_sz:
                            raw = self._dma_bufs[ping]
                            for z in range(n, chunk_sz):
                                raw[z] = 0
                        i2s.write(mv)
                        return
                self._stream_mode = 0
                i2s.write(self._silence)
            elif mode == 2:  # _MODE_TONE
                buf = self._tone_buf
                if buf is not None:
                    self._dma_inflight_idx = self._dma_active_idx
                    i2s.write(buf)
                    return
                i2s.write(self._silence)
            else:
                i2s.write(self._silence)
        except Exception:
            # Always keep DMA IRQ chain fed with silence so I2S never stalls
            self._stream_mode = 0
            try:
                i2s.write(self._silence)
            except Exception:
                pass

    def _ensure_ready(self):
        self._shutdown_stage = 0
        was_enabled = self.amp.is_enabled()
        need_init = (not was_enabled) or (not self._amp_configured)

        # Continuous 44.1 kHz I2S bitclock/FSYNC has been running since boot,
        # so TAS2780 wakes cleanly without missing-clock mute or blocking sleeps.
        if need_init:
            if was_enabled and not self._amp_configured:
                try:
                    self._pin_en.value(False)
                    time.sleep_ms(2)
                except Exception:
                    pass
            try:
                self.amp.enable()
            except AttributeError:
                pass
            try:
                self.amp.write_i2c_reg(self._REG_PAGE, 0x00)
                self.amp.write_i2c_reg(self._REG_MODE_CTRL, self._MODE_ACTIVE)
            except Exception:
                pass
            self._apply_dvc_volume()
            self._amp_configured = True

    def _open_or_rewind_wav(self, filename):
        self._stream_mode = self._MODE_IDLE
        path = "/" + filename.lstrip("/")
        if self._wav_f is not None and self._wav_open_name == path:
            try:
                self._wav_f.seek(self._wav_data_offset)
                self._wav_rem = self._wav_data_size
                return
            except Exception:
                self._close_wav()

        self._close_wav()
        f = open(path, "rb")
        hdr = f.read(128)
        if len(hdr) < 44 or hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE":
            f.close()
            raise ValueError("Invalid WAV header")
        idx = hdr.find(b"data", 12)
        if idx < 0 or idx + 8 > len(hdr):
            f.close()
            raise ValueError("WAV data chunk not found")
        data_size = struct.unpack("<I", hdr[idx + 4 : idx + 8])[0]
        data_offset = idx + 8
        f.seek(data_offset)
        self._wav_f = f
        self._wav_open_name = path
        self._wav_data_offset = data_offset
        self._wav_data_size = data_size
        self._wav_rem = data_size

    def _close_wav(self):
        self._stream_mode = self._MODE_IDLE
        if self._wav_f is not None:
            try:
                self._wav_f.close()
            except Exception:
                pass
            self._wav_f = None
            self._wav_open_name = None
            self._wav_rem = 0

    def _build_tone_buffer(self, frequency, shape):
        if not isinstance(shape, (list, tuple)):
            shape = (shape,)
        spc = max(8, self.SAMPLE_RATE // int(frequency))

        # Select the inactive ping-pong buffer not currently latched in DMA
        base_idx = self._dma_active_idx if self._tone_buf is not None else self._dma_inflight_idx
        idx = base_idx ^ 1

        buf = self._dma_bufs[idx]
        mv = self._dma_mvs[idx]
        max_samples = len(buf) // 2
        waves = max(1, max_samples // spc)
        total = spc * waves

        # Synthesise a single wave cycle, then tile across ~10-12 ms DMA chunk
        two_pi = 2.0 * math.pi
        use_sin = self.SINE in shape
        use_sq = self.SQUARE in shape
        use_tri = self.TRI in shape
        for i in range(spc):
            s = 0.0
            if use_tri:
                s += (((i - (spc >> 1)) / spc) * 0.55)
            if use_sin:
                s += math.sin(two_pi * i / spc) * 0.90
            if use_sq:
                s += (0.24 if i < (spc >> 1) else -0.24)
            if s > 1.0:
                s = 1.0
            elif s < -1.0:
                s = -1.0
            struct.pack_into("<h", buf, i * 2, int(s * 27000))

        cycle_bytes = spc * 2
        if waves > 1:
            unit = mv[:cycle_bytes]
            off = cycle_bytes
            end = total * 2
            while off < end:
                mv[off : off + cycle_bytes] = unit
                off += cycle_bytes

        return idx, mv[: total * 2]

    def set_volume(self, volume):
        self.volume = max(0.0, min(1.0, float(volume)))
        self._apply_dvc_volume()
        return self.get_status()

    def play(self, sound_name):
        name = str(sound_name).lower()
        if name not in self.PATTERNS and name not in self.WAV_FILES:
            raise ValueError("Unknown sound: {}".format(name))
        if not self.yukon.is_main_output_enabled():
            return {"ok": False, "error": "Main power rail is off"}
        if self._busy:
            return self.get_status()

        self._busy = True
        try:
            self._stream_mode = self._MODE_IDLE
            self._ensure_ready()

            if name in self.WAV_FILES:
                wav_name, count = self.WAV_FILES[name]
                try:
                    self._open_or_rewind_wav(wav_name)
                    self.active_sound = name
                    self._play_kind = "wav"
                    self._wav_repeats_left = max(0, int(count) - 1)
                    self._wav_gap_active = False
                    self._last_active_ms = time.ticks_ms()
                    self._stream_mode = self._MODE_WAV
                    return self.get_status()
                except Exception:
                    pass

            self.active_sound = name
            self._play_kind = "tone"
            self._seq = self.PATTERNS[name]
            self._idx = 0
            self._start_current_tone_step()
            return self.get_status()
        finally:
            self._busy = False

    def _start_current_tone_step(self):
        now = time.ticks_ms()
        self._last_active_ms = now
        if self._idx >= len(self._seq):
            self.stop()
            return

        freq, dur_ms, shape = self._seq[self._idx]
        self._step_end_ms = time.ticks_add(now, int(dur_ms))
        if freq > 0:
            # Synthesise into the inactive ping-pong buffer while previous note streams without a gap
            idx, new_mv = self._build_tone_buffer(int(freq), shape)
            self._tone_buf = new_mv
            self._dma_active_idx = idx
            self._stream_mode = self._MODE_TONE
        else:
            self._stream_mode = self._MODE_IDLE
            self._tone_buf = None

    def stop(self):
        self._stream_mode = self._MODE_IDLE
        self.active_sound = None
        self._play_kind = None
        self._seq = ()
        self._idx = 0
        self._wav_repeats_left = 0
        self._wav_gap_active = False
        self._tone_buf = None
        self._last_active_ms = time.ticks_ms()
        return self.get_status()

    def _teardown_i2s(self):
        # Silence stream and close WAV handle without calling I2S.deinit()
        # (deinit disables RP2040 DMA_IRQ_0 shared with CYW43 Wi-Fi).
        self.stop()
        self._close_wav()

    def _write_mute_stage1(self):
        try:
            self.amp.write_i2c_reg(self._REG_PAGE, 0x00)
            self.amp.write_i2c_reg(self._REG_DVC, self._DVC_MUTE)
            self.amp.write_i2c_reg(self._REG_MODE_CTRL, self._MODE_ACTIVE_MUTED)
        except Exception:
            pass

    def _write_sw_shutdown_stage2(self):
        try:
            self.amp.write_i2c_reg(self._REG_PAGE, 0x00)
            self.amp.write_i2c_reg(self._REG_MODE_CTRL, self._MODE_SW_SHUTDOWN)
        except Exception:
            pass

    def _finish_hw_disable(self):
        self._shutdown_stage = 0
        self._amp_configured = False
        try:
            self.amp.disable()
        except Exception:
            try:
                self._pin_en.value(False)
            except Exception:
                pass
        gc.collect()

    def _shutdown_hardware(self):
        self._teardown_i2s()
        # Ramp DVC -> Active Mute -> Software Shutdown (Hi-Z) while I2S clocks &
        # 12V PVDD are still present so pulling SDZ low never clicks/pops.
        if self.amp.is_enabled() and self.yukon.is_main_output_enabled():
            if self._shutdown_stage < 1:
                self._write_mute_stage1()
                time.sleep_ms(20)
            if self._shutdown_stage < 2:
                self._write_sw_shutdown_stage2()
                time.sleep_ms(30)
        self._finish_hw_disable()

    def tick(self):
        if not self.yukon.is_main_output_enabled():
            if self.active_sound is not None or self.amp.is_enabled():
                self._teardown_i2s()
                self._finish_hw_disable()
            return

        now = time.ticks_ms()
        if self.active_sound is not None:
            self._shutdown_stage = 0
            if self._play_kind == "wav":
                if self._stream_mode == self._MODE_WAV:
                    self._last_active_ms = now
                elif self._wav_repeats_left > 0:
                    if not self._wav_gap_active:
                        self._wav_gap_active = True
                        self._step_end_ms = time.ticks_add(now, 35)
                    elif time.ticks_diff(now, self._step_end_ms) >= 0:
                        self._wav_gap_active = False
                        self._wav_repeats_left -= 1
                        try:
                            wav_name = self.WAV_FILES[self.active_sound][0]
                            self._open_or_rewind_wav(wav_name)
                            self._last_active_ms = now
                            self._stream_mode = self._MODE_WAV
                        except Exception:
                            self.stop()
                else:
                    self.active_sound = None
                    self._play_kind = None
                    self._last_active_ms = now
            else:
                if time.ticks_diff(now, self._step_end_ms) >= 0:
                    self._idx += 1
                    self._start_current_tone_step()
        elif self.amp.is_enabled():
            if self._shutdown_stage == 0:
                if time.ticks_diff(now, self._last_active_ms) >= self.IDLE_SHUTDOWN_MS:
                    self._teardown_i2s()
                    self._amp_configured = False
                    self._write_mute_stage1()
                    self._shutdown_stage = 1
                    self._shutdown_step_ms = time.ticks_add(now, self.SOFT_RAMP_STEP_MS)
            elif self._shutdown_stage == 1:
                if time.ticks_diff(now, self._shutdown_step_ms) >= 0:
                    self._write_sw_shutdown_stage2()
                    self._shutdown_stage = 2
                    self._shutdown_step_ms = time.ticks_add(now, self.SOFT_RAMP_STEP_MS)
            elif self._shutdown_stage == 2:
                if time.ticks_diff(now, self._shutdown_step_ms) >= 0:
                    self._finish_hw_disable()

    def get_status(self):
        return {
            "playing": self.active_sound,
            "volume": round(self.volume, 2),
            "gain_db": self.gain_db,
            "amp_enabled": self.amp.is_enabled(),
        }
