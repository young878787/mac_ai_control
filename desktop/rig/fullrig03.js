// runtime-fullrig03 這個模型的事實：語意名稱（domain/character.js 說的）→ 模型裡的名稱，加上參數 ID 與版面。
// 換模型＝新增一份同形狀的檔案並把 MAC_AI_UI_MODEL 指向新模型，狀態機不用動；rig/check.js 的 checkRig 驗證涵蓋度。
export default {
  motions: { idle: 'Idle', surprised: 'Surprised', curious: 'Curious', nod: 'Nod', shake: 'Shake', happy: 'Happy', talk: 'Talk' },   // 不載入 Blink：它會把 Idle 的姿勢歸零
  expressions: { neutral: 'neutral', happy: 'happy', pout: 'pout', sleepy: 'sleepy' },                                              // 必要：缺了拋錯
  optional: { surprised: 'surprised', starEyes: 'star_eyes', poutLeft: 'pout_left' },                                               // 選用：缺了只警告，狀態機要求時不演
  gestures: { armLeftUp: 'arm_l_up', armRightUp: 'arm_r_up', chinRest: 'chin_rest' },                                               // 手勢層（手臂切換參數），和臉部表情分開排隊
  params: { angleX: 'ParamAngleX', angleY: 'ParamAngleY', angleZ: 'ParamAngleZ', bodyZ: 'ParamBodyAngleZ',
    eyeX: 'ParamEyeBallX', eyeY: 'ParamEyeBallY', eyeOpen: 'ParamEyeLOpen' },
  layout: { region: { x: 380, y: 0, w: 1280, h: 2048 }, height: 420, headFraction: 0.30 },   // region＝模型座標中的「全身」範圍；height＝出場時模型高度（px）；headFraction＝模型頂端往下這個比例內算「頭部」
};
