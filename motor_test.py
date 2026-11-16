# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      motor_test.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Slew-ramped traction motor qualification & Coulomb telemetry
#              bench test for JGA25-370DG drive unit (Slot 1 Motor 1).
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import time
from pimoroni_yukon import Yukon
from pimoroni_yukon import SLOT1, SLOT2, SLOT4, SLOT5, SLOT6
from pimoroni_yukon.modules import DualMotorModule, LEDStripModule, RM2WirelessModule, AudioAmpModule
from drivers.battery import BatteryMonitor
import config


def ramp_motor(motor, battery, start_speed, target_speed, duration_ms=500, steps=10):
    step_ms = max(10, duration_ms // steps)
    for i in range(1, steps + 1):
        spd = start_speed + (target_speed - start_speed) * (i / steps)
        motor.speed(spd)
        time.sleep_ms(step_ms)
        battery.update()


def run_motor_test(peak_speed=0.25, hold_ms=1000):
    print("\n" + "=" * 60)
    print("   JGA25-370DG RAMPED MOTOR & BATTERY TELEMETRY TEST")
    print("=" * 60)

    yukon = Yukon()

    m1 = DualMotorModule()
    m2 = DualMotorModule()
    leds = LEDStripModule(LEDStripModule.NEOPIXEL, 0, 0, config.NUM_LEDS, config.LED_BRIGHTNESS)
    rm2 = RM2WirelessModule()
    amp = AudioAmpModule(config.AUDIO_I2S_ID)

    yukon.register_with_slot(m1, SLOT1)
    yukon.register_with_slot(m2, SLOT2)
    yukon.register_with_slot(leds, SLOT4)
    yukon.register_with_slot(rm2, SLOT5)
    yukon.register_with_slot(amp, SLOT6)

    try:
        print("[1/4] Verifying Yukon module slots & taring current sensor...")
        yukon.verify_and_initialise()

        # Tare 0A offset BEFORE enabling main output power rail
        battery = BatteryMonitor(yukon, capacity_mah=config.BATTERY_CAPACITY_MAH)
        init_stat = battery.get_status()
        print("      0A Tare Offset: {:.3f}A".format(init_stat["zero_offset_a"]))
        print("      Resting Pack:   {:.2f}V (~{:.1f}% SoC, {:.0f} mAh est. remaining)".format(
            init_stat["voltage_v"], init_stat["soc_percent"], init_stat["remaining_mah"]
        ))

        # DRV8424 discrete limit: 1.2A selects 1.143A step (1.0A selects 0.786A step)
        m1.set_current_limit(config.MOTOR_CURRENT_LIMIT_AMPS)
        m2.set_current_limit(config.MOTOR_CURRENT_LIMIT_AMPS)
        print("      DRV8424 Limit:  {:.3f}A".format(m1.current_limit()))

        print("[2/4] Enabling main power rail (e-Fuse active)...")
        yukon.enable_main_output()
        time.sleep_ms(100)
        idle_stat = battery.update()
        print("      Idle Rail:      {:.2f}V | Calibrated Current: {:.3f}A".format(
            idle_stat["voltage_v"], idle_stat["current_a"]
        ))

        m1.enable()
        motor = m1.motor1

        print("\n[3/4] Smooth FORWARD ramp (0 -> {:.0f}% -> 0)...".format(peak_speed * 100))
        ramp_motor(motor, battery, 0.0, peak_speed, duration_ms=400)
        samples = max(1, hold_ms // 200)
        for i in range(samples):
            time.sleep_ms(200)
            st = battery.update()
            print("      FWD {:.0f}% | V={:.2f}V | I={:.3f}A | T_drv={:.1f}C".format(
                peak_speed * 100, st["voltage_v"], st["current_a"], m1.read_temperature()
            ))
        ramp_motor(motor, battery, peak_speed, 0.0, duration_ms=400)
        motor.stop()
        time.sleep_ms(500)

        print("\n[4/4] Smooth REVERSE ramp (0 -> -{:.0f}% -> 0)...".format(peak_speed * 100))
        ramp_motor(motor, battery, 0.0, -peak_speed, duration_ms=400)
        for i in range(samples):
            time.sleep_ms(200)
            st = battery.update()
            print("      REV {:.0f}% | V={:.2f}V | I={:.3f}A | T_drv={:.1f}C".format(
                peak_speed * 100, st["voltage_v"], st["current_a"], m1.read_temperature()
            ))
        ramp_motor(motor, battery, -peak_speed, 0.0, duration_ms=400)
        motor.stop()
        m1.disable()

        final_st = battery.update()
        print("\n--> TEST COMPLETE: V={:.2f}V | SoC={:.1f}% | Used={:.2f} mAh".format(
            final_st["voltage_v"], final_st["soc_percent"], final_st["consumed_mah"]
        ))

    except KeyboardInterrupt:
        print("\nTest stopped by user.")
    finally:
        print("Safely resetting Yukon power rails...")
        yukon.reset()


if __name__ == "__main__":
    run_motor_test()
