// Live2D 官方範例模型 hiyori_pro_t11 的事實，形狀同 fullrig03.js。模型沒有任何表情檔，所以表情全部列在 optional（缺了只警告、不演）。
// 動作用 model3.json 各群組的第一個；對應依動作曲線挑的（頭部擺動方向、嘴、眼、手臂）。
export default {
  motions: { idle: 'Idle', surprised: 'Tap', curious: 'Flick', nod: 'Tap@Body', shake: 'FlickDown', happy: 'FlickUp', talk: 'Flick@Body' },
  once: ['surprised', 'curious', 'nod', 'shake', 'happy'],   // 動作檔全部標 Loop，這幾個反應只播一次才回得到待機
  expressions: {},
  optional: { neutral: 'neutral', happy: 'happy', pout: 'pout', sleepy: 'sleepy', surprised: 'surprised', starEyes: 'star_eyes', poutLeft: 'pout_left' },
  gestures: { armLeftUp: 'arm_l_up', armRightUp: 'arm_r_up', chinRest: 'chin_rest' },
  params: { angleX: 'ParamAngleX', angleY: 'ParamAngleY', angleZ: 'ParamAngleZ', bodyZ: 'ParamBodyAngleZ',
    eyeX: 'ParamEyeBallX', eyeY: 'ParamEyeBallY', eyeOpen: 'ParamEyeLOpen',
    bodyX: 'ParamBodyAngleX', bodyY: 'ParamBodyAngleY', eyeROpen: 'ParamEyeROpen',
    browLY: 'ParamBrowLY', browRY: 'ParamBrowRY', browLX: 'ParamBrowLX', browRX: 'ParamBrowRX',
    browLForm: 'ParamBrowLForm', browRForm: 'ParamBrowRForm', browLAngle: 'ParamBrowLAngle', browRAngle: 'ParamBrowRAngle', breath: 'ParamBreath' },
  layout: { region: { x: 634, y: 200, w: 830, h: 2610 }, height: 420, headFraction: 0.19 },   // region 以「模型畫布寬＝2048」的座標表示（animator.js）
};
