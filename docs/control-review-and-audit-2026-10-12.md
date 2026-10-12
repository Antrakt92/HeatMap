# Independent control review and continued audit

## Decision

Retain a hardware driver curve, independent Core/Hotspot/Memory protection,
Zero RPM handback and bounded normal acceleration. Replace accumulated thermal
error with a small, history-independent proportional correction. The previous
integrator had no measured thermal-delay model or actuator tracking; it cannot
be described as an optimal controller for this board.

Reproduction: constant Core 64 C, Hotspot 80 C and memory 75 C reached 100%
software demand after 52 seconds. Returning to 60/80/80 C retained 100% indefinitely.
A reversed time step could also turn a fail-safe 100% request into 90% through a
negative acceleration budget. Both were reproduced before repair.

The new request is the greatest protective-curve demand plus up to 10 percentage
points for soft target exceedance. Targets remain Core 60 C and Hotspot/memory
80 C, with normalized tolerances 3/2/2 C. The correction has an ordinary 95%
ceiling; it never reduces the independent protective base. Full temperatures
75/90/90 C and invalid/missing/stopped feedback retain immediate protection.
Ordinary increases remain 10 points/s, cooling hold 10 seconds and fall 2 points/s.
At 64/80/75 C demand remains 67%, independent of how long the workload lasts.
After an emergency, target-band demand returns to 65%; the command still retains
its cooling hold and gradual fall. The saved driver profile and Zero RPM are unchanged.

Independent synthetic stress: a first-order thermal model with a 60-second time
constant and 30-second cooling delay exposed approximately 59-100% duty cycling
with the prior integrator, while the bounded correction settled around 75-78%
in the same model. These are algorithm checks with assumed dynamics, not observed
oscillation or demonstrated noise/temperature gains on the user's GPU. They do
not establish universal stability or physical optimum.

## Further confirmed repairs

- B850's automatic-mode duty register can retain an old manual byte. Startup
  now seeds smoothing from measured SYS RPM / commissioned full RPM instead.
- All exposed GPU tachometers contribute to assistance and previously running
  channel-loss/stop protection. CPU identities, selected GPU ID/name and model
  ambiguity are checked through the session, not just at startup.
- Complete identical LHM readings formerly forced 100% after six seconds and
  stopped control after fifteen. Value constancy proves neither freshness nor
  freezing; this false inference is removed. Missing/invalid feedback, native
  read errors and owner guards after reads remain active.
- Known interrupted case ownership is recovered before waiting for normal
  tachs/CPU/GPU readiness. Unknown journal presence fails closed. Handback
  confirms automatic mode before retained manual duty and attempts every owned
  channel even if an earlier restore fails. Unconfirmed channels retain evidence.
- Commissioning cannot force its 60% minimum phase through a thermal/fan emergency.
  All tachs must run and show at least 5% fall after six seconds of settling
  before minimum-duty verification is accepted. No commissioning was run live.
- Profile commit errors reconcile the actual durable journal. Known committed
  profiles are retained; pre-commit failures still restore the old curve; unknown
  outcomes and repeated interruptions preserve settings and recovery evidence.
- Partial overlay construction now stops both fan owners before waiting, uses
  one 15-second cleanup budget, checks terminal restoration evidence, joins
  started monitor threads and destroys Tk even if callback cancellation fails.
  It preserves the original error and does not save incomplete UI preferences.
- Storage-process construction/start failures close owned resources. Pipe-close
  failure cannot skip child termination/reaping. Signature JSON must be an object.
- The CPU RPM-reference dialog no longer claims it affects display only: B850
  assistance uses the saved reference at its next startup when duty is unavailable.

## Verification and limits

1064 local tests passed, including 47 additional regression/model tests. The
relevant prior failures were reproduced before repair. Full-worker tests verify
normal ramping, emergency clearance, repeated timestamps, ownership, partial
restoration, commit boundaries and exact saved-curve/Zero RPM handback.
Compilation, runtime integrity/preflight, manifest synchronization and whitespace
checks passed. Off-screen Win32 desktop integration passed. Read-only volume
integration confirmed C/D/E/F capacity/mapping; the synthetic blocked probe was
reaped in 2.21 seconds. No drivers, DLLs or dependencies were replaced.

Normal elevated restart completed after both fan-owner mutexes were released.
Fresh GPU status showed standby, Core/Hotspot 46 C, memory 62 C and 0 RPM. Case
SYS1/2/3 remained active, assisting the CPU fan fraction. A guarded read-only ADLX
snapshot confirmed the same balanced five points and Zero RPM true. This is
startup/read-back evidence only; no new load/noise measurement was performed.

Hardware-free assertions do not prove all physical GPU rotors turn: ADLX exposes
one common RPM and LHM may expose only one channel. Nor does a live LHM return
independently prove non-cached values. After worker death the driver retains its
hardware curve; the driver input's mapping to Core/Hotspot remains unspecified,
so independent software protections are not claimed to remain live without the
worker. No new game, stress test or long-running sampler was started.

Primary design references: [bounded PID and actuator feedback](https://www.mathworks.com/help/simulink/slref/anti-windup-control-using-a-pid-controller.html),
[ADLX fan tuning states](https://gpuopen.com/manuals/adlx/adlx-sdk-references/adlx-interfaces/gpu-tuning/iadlxmanualfantuning/setfantuningstates/).
Numeric temperature/duty choices are application policy, not manufacturer-certified optimum.
