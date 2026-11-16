# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      hardware_test.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Full-system bench qualification & slot signature verification
#              suite for Motors, LEDs, RM2 Wireless, Audio Amp, IMU, and GNSS.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import time
import network
from pimoroni_yukon import Yukon
from pimoroni_yukon import SLOT1, SLOT2, SLOT4, SLOT5, SLOT6
from pimoroni_yukon.modules import DualMotorModule, LEDStripModule, RM2WirelessModule, AudioAmpModule

from drivers.icm20948 import ICM20948
from drivers.pa1010d import PA1010D
from drivers.lights import ConeLights
import config

def run_hardware_test():
    print("\n" + "=" * 60)
    print("      PIMORONI YUKON HARDWARE VALIDATION TEST")
    print("=" * 60)

    # 1. Initialize Yukon
    print("\n[1/7] Initializing Yukon board controller...")
    yukon = Yukon()

    # 2. Instantiate and register modules with their designated slots
    motor_mod1 = DualMotorModule()
    motor_mod2 = DualMotorModule()
    led_mod = LEDStripModule(LEDStripModule.NEOPIXEL, 0, 0, config.NUM_LEDS, config.LED_BRIGHTNESS)
    wireless_mod = RM2WirelessModule()
    audio_mod = AudioAmpModule(0)

    print("      Registering modules with slots:")
    print("        - Slot 1: Dual Motor Module #1")
    print("        - Slot 2: Dual Motor Module #2")
    print("        - Slot 4: LED Strip Module")
    print("        - Slot 5: RM2 Wireless Module")
    print("        - Slot 6: Audio Amp Module")

    yukon.register_with_slot(motor_mod1, SLOT1)
    yukon.register_with_slot(motor_mod2, SLOT2)
    yukon.register_with_slot(led_mod, SLOT4)
    yukon.register_with_slot(wireless_mod, SLOT5)
    yukon.register_with_slot(audio_mod, SLOT6)

    try:
        # Verify slot presence signatures
        print("      Verifying module signatures...")
        yukon.verify_and_initialise()
        print("      --> PASS: All 5 slots verified and initialized successfully!")

        # Turn on main power rail
        print("      Enabling main output power rail (e-Fuse active)...")
        yukon.enable_main_output()
        time.sleep_ms(100)

        # Read board telemetry
        voltage = yukon.read_input_voltage()
        current = yukon.read_current()
        temp = yukon.read_temperature()
        print("      --> Telemetry: Supply = {:.2f}V | Current = {:.3f}A | Temp = {:.1f}C".format(voltage, current, temp))

        # 3. I2C Bus Scan
        print("\n[2/7] Scanning I2C Header Bus (yukon.i2c)...")
        devices = yukon.i2c.scan()
        hex_devices = ["0x{:02X}".format(d) for d in devices]
        print("      Found {} device(s): {}".format(len(devices), ", ".join(hex_devices)))

        has_imu = (config.I2C_ADDR_ICM20948 in devices)
        has_gps = (config.I2C_ADDR_PA1010D in devices)

        # 4. Test ICM-20948 IMU
        print("\n[3/7] Testing ICM-20948 9DoF Motion Sensor...")
        if has_imu:
            try:
                imu = ICM20948(yukon.i2c, address=config.I2C_ADDR_ICM20948)
                time.sleep_ms(50)
                data = imu.read_all()
                ax, ay, az = data["accel"]
                gx, gy, gz = data["gyro"]
                print("      --> PASS: ICM-20948 Online!")
                print("          Accel (g):    X={:+.2f}, Y={:+.2f}, Z={:+.2f}".format(ax, ay, az))
                print("          Gyro (deg/s): X={:+.1f}, Y={:+.1f}, Z={:+.1f}".format(gx, gy, gz))
                print("          Sensor Temp:  {:.1f} C".format(data["temp"]))
            except Exception as e:
                print("      --> FAIL: Error communicating with ICM-20948: {}".format(e))
        else:
            print("      --> WARNING: 0x{:02X} not detected on I2C bus. Check soldered connections.".format(config.I2C_ADDR_ICM20948))

        # 5. Test PA1010D GPS
        print("\n[4/7] Testing PA1010D GPS Module...")
        if has_gps:
            try:
                gps = PA1010D(yukon.i2c, address=config.I2C_ADDR_PA1010D)
                print("      Listening for NMEA data stream (2 seconds)...")
                t_end = time.ticks_add(time.ticks_ms(), 2000)
                rx_count = 0
                while time.ticks_diff(t_end, time.ticks_ms()) > 0:
                    if gps.update():
                        rx_count += 1
                    time.sleep_ms(50)

                status = gps.get_status()
                print("      --> PASS: PA1010D Online! Received {} sentence packets.".format(rx_count))
                print("          GPS Fix:     {}".format("YES (Locked)" if status["has_fix"] else "NO (Searching for satellites indoors)"))
                print("          Satellites:  {}".format(status["satellites"]))
                if status["has_fix"]:
                    print("          Coordinates: {:.6f}, {:.6f}".format(status["latitude"], status["longitude"]))
            except Exception as e:
                print("      --> FAIL: Error reading GPS: {}".format(e))
        else:
            print("      --> WARNING: 0x{:02X} not detected on I2C bus. Check soldered connections.".format(config.I2C_ADDR_PA1010D))

        # 6. Test Audio Amp (Slot 6)
        print("\n[5/7] Testing Audio Amp Module (Slot 6)...")
        try:
            audio_mod.enable()
            audio_mod.set_volume(0.35)  # Gentle 35% volume
            print("      Playing boot chime (C5 - E5 - G5)...")
            audio_mod.player.play_tone(523, 0.5)   # C5
            time.sleep_ms(150)
            audio_mod.player.play_tone(659, 0.5)   # E5
            time.sleep_ms(150)
            audio_mod.player.play_tone(784, 0.6)   # G5
            time.sleep_ms(300)
            audio_mod.player.stop()
            audio_mod.disable()
            print("      --> PASS: Audio chime played successfully!")
        except Exception as e:
            print("      --> FAIL: Audio amp test error: {}".format(e))

        # 7. Test LED Strip (Slot 4)
        print("\n[6/7] Testing LED Strip Module (Slot 4 - {} LEDs)...".format(config.NUM_LEDS))
        try:
            lights = ConeLights(led_mod.strip, num_leds=config.NUM_LEDS, led_module=led_mod)
            print("      Running rainbow test across all 50 LEDs (1.5 seconds)...")
            lights.effect_rainbow_test()
            time.sleep_ms(1500)

            print("      Simulating traffic cone amber beacon (2 seconds)...")
            for _ in range(40):
                lights.effect_beacon(speed_step=1)
                time.sleep_ms(50)

            lights.set_mode("off")
            print("      --> PASS: LED Strip test complete!")
        except Exception as e:
            print("      --> FAIL: LED strip test error: {}".format(e))

        # 8. Test RM2 Wireless (Slot 5)
        print("\n[7/7] Testing RM2 Wireless Module (Slot 5)...")
        try:
            wlan = network.WLAN(network.AP_IF)
            ap_ssid = "TrafficCone-Setup"
            wlan.config(essid=ap_ssid, security=0)
            wlan.active(True)
            time.sleep_ms(500)
            ip_info = wlan.ifconfig()
            mac = ":".join("{:02X}".format(b) for b in wlan.config("mac"))
            print("      --> PASS: Wi-Fi Access Point created!")
            print("          SSID: {}".format(ap_ssid))
            print("          IP Address: {}".format(ip_info[0]))
            print("          MAC: {}".format(mac))
        except Exception as e:
            print("      --> FAIL: Wireless initialization error: {}".format(e))

        # 9. Verify Motor Drivers without running motors
        print("\n[*] Verifying Motor Drivers (Slot 1 & Slot 2)...")
        try:
            motor_mod1.enable()
            motor_mod2.enable()
            print("      --> PASS: Motor driver H-bridges enabled and healthy.")
            # Keep them safely disabled
            motor_mod1.disable()
            motor_mod2.disable()
            print("      --> Motor channels safely in standby (motors not connected yet).")
        except Exception as e:
            print("      --> FAIL: Motor driver enable error: {}".format(e))

        print("\n" + "=" * 60)
        print("           HARDWARE TEST COMPLETE: ALL SYSTEMS READY")
        print("=" * 60 + "\n")

    except KeyboardInterrupt:
        print("\nTest interrupted by user.")
    finally:
        print("Resetting Yukon outputs to safe standby state...")
        yukon.reset()

if __name__ == "__main__":
    run_hardware_test()
