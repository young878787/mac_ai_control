/** Fixed 60 Hz adapter for this model's independent, full-weight hair outputs.
 * The pinned CubismPhysics solver is unchanged. Rendering interpolates its last
 * two fixed-step outputs, intentionally adding one physics tick of latency.
 */
export function createFixedPhysics(solver, core, physicsJson) {
  const STEP = 1 / 60;
  const raw = core.getModel().parameters;
  const ids = Array.from(raw.ids);
  const inputs = [...new Set(physicsJson.PhysicsSettings.flatMap(s => s.Input.map(i => i.Source.Id)))];
  const outputs = physicsJson.PhysicsSettings.flatMap(s => s.Output);
  const outputIds = outputs.map(o => o.Destination.Id);
  if (new Set(outputIds).size !== outputIds.length || outputs.some(o => o.Weight !== 100) ||
      inputs.some(id => outputIds.includes(id))) {
    throw new Error('Fixed physics requires independent, unique, full-weight outputs');
  }
  const inputIndices = inputs.map(id => ids.indexOf(id));
  const outputIndices = outputIds.map(id => ids.indexOf(id));
  if ([...inputIndices, ...outputIndices].some(index => index < 0)) {
    throw new Error('Physics references an absent native parameter');
  }
  let remainder = 0;
  let inputCache, previousOutput, currentOutput;
  const diagnostics = {
    implementation: 'RushiaHD fixed-step adapter over the unchanged Pixi CubismPhysics solver',
    simulationHz: 60,
    outputLatencySeconds: STEP,
    inputIds: inputs,
    outputIds,
    frames: 0,
    simulationSteps: 0,
    elapsedSeconds: 0,
  };

  function initialize() {
    solver.initialize();
    remainder = 0;
    inputCache = Float64Array.from(inputIndices, index => raw.values[index]);
    currentOutput = Float64Array.from(outputIndices, index => raw.values[index]);
    previousOutput = Float64Array.from(currentOutput);
    diagnostics.frames = diagnostics.simulationSteps = diagnostics.elapsedSeconds = 0;
  }

  function evaluate(model, deltaSeconds) {
    if (model !== core) throw new Error('Physics clock belongs to a different Core model');
    if (!Number.isFinite(deltaSeconds) || deltaSeconds < 0) throw new Error('Invalid physics delta seconds');
    if (deltaSeconds === 0) return;
    if (deltaSeconds > 0.25) throw new Error('Clamp suspended render frames before advancing the physics clock');
    const renderValues = Float32Array.from(raw.values);
    remainder += deltaSeconds;
    diagnostics.frames++;
    diagnostics.elapsedSeconds += deltaSeconds;
    try {
      while (remainder + 1e-12 >= STEP) {
        // As in the modern Framework scheduler, interpolate from the previous
        // simulation input to the newest render input at each fixed tick.
        const weight = Math.min(1, STEP / remainder);
        raw.values.set(renderValues);
        for (let i = 0; i < inputIndices.length; i++) {
          const index = inputIndices[i];
          inputCache[i] += (renderValues[index] - inputCache[i]) * weight;
          raw.values[index] = inputCache[i];
        }
        previousOutput.set(currentOutput);
        solver.evaluate(core, STEP);
        for (let i = 0; i < outputIndices.length; i++) {
          currentOutput[i] = raw.values[outputIndices[i]];
          if (!Number.isFinite(currentOutput[i])) throw new Error('Nonfinite fixed-step physics output');
        }
        remainder = Math.max(0, remainder - STEP);
        diagnostics.simulationSteps++;
      }
    } finally {
      // Interpolated inputs are temporary. Keep the current head/body/face pose.
      raw.values.set(renderValues);
    }
    const alpha = Math.min(1, remainder / STEP);
    for (let i = 0; i < outputIndices.length; i++) {
      raw.values[outputIndices[i]] = previousOutput[i] * (1 - alpha) + currentOutput[i] * alpha;
    }
  }

  initialize();
  return { initialize, evaluate, diagnostics };
}
