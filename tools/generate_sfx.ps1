# ==============================================================================
# ARSE - Autonomous Road Systems Engineering
# Proprietary & Confidential - Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Script:      tools/generate_sfx.ps1
# Description: Formant & harmonic acoustic synthesizer for ARSE Mk.I "MEEP!"
#              pedestrian advisement vocalization (44.1 kHz, 16-bit Mono PCM).
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$legacySounds = Join-Path $projectRoot "sounds"
if (Test-Path $legacySounds) {
    Remove-Item -Recurse -Force $legacySounds
}
$outPath = Join-Path $projectRoot "meep.wav"

# Compile fast C# acoustic formant synthesizer in memory for exact 64-bit DSP
Add-Type -TypeDefinition @"
using System;
using System.IO;

public static class ArseMeepSynth {
    private class BiquadBandpass {
        private double b0, b1, b2, a1, a2;
        private double x1, x2, y1, y2;

        public void Configure(double fs, double fc, double q) {
            double w0 = 2.0 * Math.PI * fc / fs;
            double alpha = Math.Sin(w0) / (2.0 * q);
            double cosw0 = Math.Cos(w0);
            double a0 = 1.0 + alpha;
            b0 = alpha / a0;
            b1 = 0.0;
            b2 = -alpha / a0;
            a1 = (-2.0 * cosw0) / a0;
            a2 = (1.0 - alpha) / a0;
        }

        public double Process(double x) {
            double y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
            x2 = x1; x1 = x;
            y2 = y1; y1 = y;
            return y;
        }
    }

    public static void GenerateMeepWav(string path) {
        const int sampleRate = 44100;
        // Timeline (seconds):
        // 0.000 .. 0.030 : Pre-roll silence (TAS2780 clock wake margin)
        // 0.030 .. 0.068 : /m/ bilabial nasal murmur ("mmm")
        // 0.068 .. 0.208 : /i:/ bright cheeky vowel ("EEEE") with bilabial locus glides
        // 0.208 .. 0.230 : /p/ bilabial stop occlusion
        // 0.230 .. 0.244 : /p/ crisp plosive release puff
        // 0.244 .. 0.275 : Post-roll silence
        const double durationSec = 0.275;
        int numSamples = (int)(sampleRate * durationSec);
        double[] buf = new double[numSamples];

        var f1Filter = new BiquadBandpass();
        var f2Filter = new BiquadBandpass();
        var f3Filter = new BiquadBandpass();
        var burstFilter = new BiquadBandpass();
        burstFilter.Configure(sampleRate, 1850.0, 2.2);

        double phase = 0.0;
        double phase2 = 0.0;
        Random rng = new Random(2026);

        for (int n = 0; n < numSamples; n++) {
            double t = (double)n / sampleRate;
            double sample = 0.0;

            if (t >= 0.030 && t < 0.208) {
                // Pitch contour F0(t): perky cartoon "m-EEEE-p"
                double f0;
                if (t < 0.068) {
                    double u = (t - 0.030) / 0.038;
                    f0 = 370.0 + 75.0 * u; // 370 -> 445 Hz nasal onset
                } else if (t < 0.100) {
                    double u = (t - 0.068) / 0.032;
                    f0 = 445.0 + 95.0 * Math.Sin(u * Math.PI * 0.5); // scoop up to 540 Hz
                } else if (t < 0.185) {
                    double u = (t - 0.100) / 0.085;
                    f0 = 540.0 + 18.0 * Math.Sin(u * Math.PI) + 5.0 * Math.Sin(2.0 * Math.PI * 28.0 * t);
                } else {
                    double u = (t - 0.185) / 0.023;
                    f0 = 540.0 - 85.0 * u; // lip closure pitch drop
                }

                phase += f0 / sampleRate;
                if (phase >= 1.0) phase -= 1.0;

                // Subtle sympathetic nasal reed detune (adds classic "meep" character)
                phase2 += (f0 * 1.006) / sampleRate;
                if (phase2 >= 1.0) phase2 -= 1.0;

                // Harmonic-rich glottal/reed excitation (band-limited up to 14 harmonics)
                double exc = 0.0;
                for (int k = 1; k <= 14; k++) {
                    if (k * f0 >= sampleRate * 0.45) break;
                    double amp = 1.0 / Math.Pow(k, 0.82);
                    exc += amp * Math.Sin(2.0 * Math.PI * k * phase);
                    if (k >= 3 && k <= 8) {
                        exc += 0.22 * amp * Math.Sin(2.0 * Math.PI * k * phase2);
                    }
                }

                // Formant trajectories for /m/ -> /i:/ -> /p/
                double f1, f2, f3, g1, g2, g3, env;
                if (t < 0.068) {
                    // /m/ closed-lip nasal murmur
                    double u = (t - 0.030) / 0.038;
                    f1 = 300.0;
                    f2 = 1050.0;
                    f3 = 2200.0;
                    g1 = 1.15;
                    g2 = 0.12;
                    g3 = 0.05;
                    env = Math.Sin(Math.Min(1.0, u * 2.5) * Math.PI * 0.5) * 0.65;
                } else if (t < 0.094) {
                    // Rapid bilabial opening transition /m/ -> /i:/
                    double u = (t - 0.068) / 0.026;
                    double s = u * u * (3.0 - 2.0 * u);
                    f1 = 300.0 + 75.0 * s;
                    f2 = 1100.0 + 1420.0 * s; // 1100 -> 2520 Hz
                    f3 = 2200.0 + 980.0 * s;  // 2200 -> 3180 Hz
                    g1 = 0.95;
                    g2 = 0.20 + 0.95 * s;
                    g3 = 0.10 + 0.70 * s;
                    env = 0.65 + 0.35 * s;
                } else if (t < 0.186) {
                    // Bright steady /i:/ ("EEEE")
                    f1 = 375.0;
                    f2 = 2520.0;
                    f3 = 3180.0;
                    g1 = 0.90;
                    g2 = 1.15;
                    g3 = 0.80;
                    env = 1.0;
                } else {
                    // Rapid bilabial closing into /p/ stop
                    double u = (t - 0.186) / 0.022;
                    double s = u * u * (3.0 - 2.0 * u);
                    f1 = 375.0 - 120.0 * s;
                    f2 = 2520.0 - 1400.0 * s; // 2520 -> 1120 Hz
                    f3 = 3180.0 - 900.0 * s;
                    g1 = 0.90 * (1.0 - 0.5 * s);
                    g2 = 1.15 * (1.0 - 0.85 * s);
                    g3 = 0.80 * (1.0 - 0.85 * s);
                    env = Math.Cos(u * Math.PI * 0.5);
                }

                // Update biquad coefficients every 16 samples (~0.36 ms) for smooth glides
                if ((n & 15) == 0) {
                    f1Filter.Configure(sampleRate, f1, 4.2);
                    f2Filter.Configure(sampleRate, f2, 7.5);
                    f3Filter.Configure(sampleRate, f3, 8.5);
                }

                double y1 = f1Filter.Process(exc);
                double y2 = f2Filter.Process(exc);
                double y3 = f3Filter.Process(exc);
                sample = env * (g1 * y1 + g2 * y2 + g3 * y3 + 0.12 * exc);
            } else if (t >= 0.230 && t < 0.244) {
                // Crisp /p/ bilabial plosive release puff
                double u = (t - 0.230) / 0.014;
                double noise = rng.NextDouble() * 2.0 - 1.0;
                double env = Math.Exp(-u * 4.2) * Math.Sin(Math.Min(1.0, u * 8.0) * Math.PI * 0.5);
                sample = 0.28 * env * burstFilter.Process(noise);
            }

            buf[n] = sample;
        }

        // Normalize peak to 88% of 16-bit full scale with gentle soft-knee headroom
        double peak = 1e-9;
        for (int i = 0; i < numSamples; i++) {
            double a = Math.Abs(buf[i]);
            if (a > peak) peak = a;
        }

        int dataBytes = numSamples * 2;
        using (var fs = new FileStream(path, FileMode.Create, FileAccess.Write))
        using (var bw = new BinaryWriter(fs)) {
            // Standard 44-byte RIFF WAVE PCM header
            bw.Write(System.Text.Encoding.ASCII.GetBytes("RIFF"));
            bw.Write((uint)(36 + dataBytes));
            bw.Write(System.Text.Encoding.ASCII.GetBytes("WAVE"));
            bw.Write(System.Text.Encoding.ASCII.GetBytes("fmt "));
            bw.Write((uint)16);              // Subchunk1Size (PCM)
            bw.Write((ushort)1);             // AudioFormat = 1 (PCM)
            bw.Write((ushort)1);             // NumChannels = 1 (Mono)
            bw.Write((uint)sampleRate);      // SampleRate = 44100
            bw.Write((uint)(sampleRate * 2));// ByteRate
            bw.Write((ushort)2);             // BlockAlign
            bw.Write((ushort)16);            // BitsPerSample = 16
            bw.Write(System.Text.Encoding.ASCII.GetBytes("data"));
            bw.Write((uint)dataBytes);

            for (int i = 0; i < numSamples; i++) {
                double norm = (buf[i] / peak) * 0.88;
                // Gentle tanh-like saturation for extra loudness without clipping
                norm = norm / (1.0 + 0.15 * norm * norm) * 1.12;
                int pcm = (int)Math.Round(Math.Max(-1.0, Math.Min(1.0, norm)) * 30000.0);
                bw.Write((short)pcm);
            }
        }
    }
}
"@

[ArseMeepSynth]::GenerateMeepWav($outPath)
$info = Get-Item $outPath
Write-Host ("Generated " + $info.FullName + " (" + $info.Length + " bytes)") -ForegroundColor Green
