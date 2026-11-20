// The pinned pixi-live2d-display 0.4.0 renderer predates Cubism 4.2 colors.
// Read the exported Core drawable colors; do not simulate mouth parameters here.
const renderers = new WeakMap();
const uniformLocations = new WeakMap();
let installed = false;

const colorFunction = `
uniform vec3 u_drawableMultiplyColor;
uniform vec3 u_drawableScreenColor;
vec4 applyDrawableColor(vec4 color) {
  color.rgb *= u_drawableMultiplyColor;
  color.rgb = color.rgb + u_drawableScreenColor * color.a
    - color.rgb * u_drawableScreenColor;
  return color;
}
`;

export function installDrawableColors(internalModel) {
  const renderer = internalModel.renderer;
  const drawables = internalModel.coreModel.getModel().drawables;
  if (!drawables.multiplyColors || !drawables.screenColors) {
    throw new Error('Cubism Core drawable color arrays are unavailable');
  }
  renderers.set(renderer, {
    drawables,
    indices: new Map(drawables.vertexPositions.map((vertices, index) => [vertices, index])),
  });
  if (installed) return;

  const prototype = PIXI.live2d.CubismShader_WebGL.prototype;
  const loadShaderProgram = prototype.loadShaderProgram;
  const setupShaderProgram = prototype.setupShaderProgram;
  prototype.loadShaderProgram = function (vertexSource, fragmentSource) {
    // Only color draws match this expression. Mask generation remains unchanged.
    const sample = /texture2D\(s_texture0\s*,\s*v_texCoord\)\s*\*\s*u_baseColor/;
    if (sample.test(fragmentSource)) {
      fragmentSource = fragmentSource.replace('void main()', colorFunction + 'void main()')
        .replace(sample, 'applyDrawableColor(texture2D(s_texture0, v_texCoord)) * u_baseColor');
    }
    return loadShaderProgram.call(this, vertexSource, fragmentSource);
  };
  prototype.setupShaderProgram = function (...args) {
    setupShaderProgram.apply(this, args);
    const [owner, , , vertices] = args;
    if (owner.getClippingContextBufferForMask()) return;
    const gl = this.gl, program = gl.getParameter(gl.CURRENT_PROGRAM);
    let locations = uniformLocations.get(program);
    if (!locations) {
      locations = {
        multiply: gl.getUniformLocation(program, 'u_drawableMultiplyColor'),
        screen: gl.getUniformLocation(program, 'u_drawableScreenColor'),
      };
      if (locations.multiply === null || locations.screen === null) {
        throw new Error('Drawable color shader was not initialized before rendering');
      }
      uniformLocations.set(program, locations);
    }
    const state = renderers.get(owner), index = state?.indices.get(vertices);
    if (index === undefined) {
      gl.uniform3f(locations.multiply, 1, 1, 1);
      gl.uniform3f(locations.screen, 0, 0, 0);
    } else {
      const offset = index * 4;
      const multiply = state.drawables.multiplyColors, screen = state.drawables.screenColors;
      gl.uniform3f(locations.multiply, multiply[offset], multiply[offset + 1], multiply[offset + 2]);
      gl.uniform3f(locations.screen, screen[offset], screen[offset + 1], screen[offset + 2]);
    }
  };
  installed = true;
}
