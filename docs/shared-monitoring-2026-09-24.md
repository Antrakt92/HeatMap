# Shared monitoring repair

HWiNFO triggered the generic conflict branch and terminated the sensor worker.
The message also listed Ryzen Master and GCC, even though those two had separate
read exceptions. All UI readings then expired until HeatMap restarted.

All detected monitoring tools now share one `shared` read policy: HWiNFO,
CPU-Z, GCC, Ryzen Master, SIV, EasyTune and FanControl. Their combinations use the
same policy. HeatMap confirms fan-controller handback before switching modes;
its automatic fan control stays paused until restart. Additional tool arrivals
and exits do not reopen or stop a healthy shared monitor. RAM uses Windows and
the additional DDR5 SPD poller remains disabled in shared mode.

A monitor arriving between the loop's process check and initialization's guard
now triggers shared initialization on the next iteration instead of a permanent
pause. Driver installation and unverifiable process inventories still block
native access; installer messages name the installer rather than blaming every
running monitor. Unconfirmed fan restoration still blocks the transition.

Verification:

- Regression demonstrated the previous policy fails combinations of monitors.
- Guard tests cover every single, pair and triple of recognized monitoring names,
  exclusive control, and installer precedence even with all monitors present.
- Lifecycle tests cover arrival, overlapping tools, all tools exiting, the
  initialization race and fan handback. Existing restoration-failure tests pass.
- Full suite: 793 tests passed; compileall and runtime integrity verification pass.
- Elevated local restart with HWiNFO64, GCC and AMD Ryzen Master running restored
  CPU/GPU temperatures, CPU fan RPM, RAM and disks. No HeatMap fan workers ran;
  disconnected case fan control remained disabled.

This proves the policy and a short local smoke check, not indefinite stability
with arbitrary tuning tools or driver replacement during a native sensor call.
Real tool arrival/exit combinations are covered by simulation; the live check
left the user's three running tools open.
