import React from "react";
import {
  AbsoluteFill,
  AnimatedImage,
  Audio,
  Easing,
  Img,
  Sequence,
  Video,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const _resolveSrc = (s: string): string =>
  /^(https?:|file:|data:|blob:)/.test(s) ? s : staticFile(s);

export const FPS = 30;
export const VIDEO_WIDTH = 1080;
export const VIDEO_HEIGHT = 1920;

export type SubtitleChunk = {
  text: string;
  start: number;
  end: number;
};

export type GifOverlay = {
  src: string;          // file path (staticFile) or remote URL with CORS
  start: number;        // seconds
  duration: number;     // seconds
  size?: number;        // pixels (default 480)
  rotate?: number;      // entrance rotate-from angle in degrees (default -6)
};

export type ShortProps = {
  hook: string;
  hashtags: string;
  bgImageSrc: string | null;
  clipSrc: string | null;
  ttsSrc: string | null;
  bgmSrc: string | null;
  bgmVolume: number;
  ttsVolume: number;
  durationInSeconds: number;
  subtitles: SubtitleChunk[];
  gifs?: GifOverlay[];
};

const LAYOUT = {
  banner: { y: 0,    h: 288 },
  title:  { y: 288,  h: 192 },
  clip:   { y: 480,  h: 960 },
  sub:    { y: 1440, h: 288 },
  hash:   { y: 1728, h: 192 },
};

const COLOR = {
  bg:       "#0e0e0e",
  bannerBg: "#1a1a2e",
  accent:   "#f0c040",
  text:     "#ffffff",
  hashtag:  "#7b68cc",
  brand:    "#c9b8e8",
};

export const defaultProps: ShortProps = {
  hook: "AI 로봇이 단 하루 만에",
  hashtags: "#AI #로봇 #미래기술 #힙포인사이트",
  bgImageSrc: null,
  clipSrc: null,
  ttsSrc: null,
  bgmSrc: null,
  bgmVolume: 0.08,
  ttsVolume: 1.0,
  durationInSeconds: 8,
  subtitles: [
    { text: "이 로봇은",       start: 0.0, end: 1.5 },
    { text: "단 하루 만에",     start: 1.5, end: 3.0 },
    { text: "작업을 학습합니다", start: 3.0, end: 5.0 },
    { text: "충격적이죠",       start: 5.0, end: 7.0 },
  ],
  gifs: [],
};

const SUBTITLE_TOP = LAYOUT.clip.y + LAYOUT.clip.h - 200;
// GIF 앵커 — 클립 영역 중앙에서 살짝 위로 (자막 침범 방지 + 시선 균형)
const GIF_CENTER_Y = LAYOUT.clip.y + LAYOUT.clip.h / 2 - 90;

// ── Subtitle chunk view ──────────────────────────────────────────────────────

const SubtitleChunkView: React.FC<{ text: string; durationInFrames: number }> = ({
  text,
  durationInFrames,
}) => {
  const frame = useCurrentFrame();

  const enterFrames = Math.min(9, Math.max(3, Math.floor(durationInFrames * 0.4)));
  const exitFrames  = Math.min(6, Math.max(2, Math.floor(durationInFrames * 0.25)));
  const exitStart   = Math.max(0, durationInFrames - exitFrames);

  const enter = interpolate(frame, [0, enterFrames], [0, 1], {
    easing: Easing.bezier(0.34, 1.56, 0.64, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const exit = interpolate(frame, [exitStart, durationInFrames], [0, 1], {
    easing: Easing.in(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const opacity    = Math.max(0, enter - exit);
  const scale      = interpolate(enter, [0, 1], [0.82, 1]);
  const translateY = interpolate(enter, [0, 1], [22, 0]);

  return (
    <AbsoluteFill style={{
      pointerEvents: "none",
      justifyContent: "flex-start",
      alignItems: "center",
    }}>
      <div style={{
        position: "absolute",
        top: SUBTITLE_TOP,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        padding: "0 60px",
        opacity,
        transform: `translateY(${translateY}px) scale(${scale})`,
      }}>
        <span style={{
          color: COLOR.accent,
          fontSize: 82,
          fontWeight: 700,
          textAlign: "center",
          textShadow: "0 4px 14px rgba(0,0,0,0.95), 0 0 6px rgba(0,0,0,0.95)",
          fontFamily: "Gmarket Sans TTF, Pretendard, sans-serif",
          letterSpacing: -1.5,
          lineHeight: 1.05,
        }}>
          {text}
        </span>
      </div>
    </AbsoluteFill>
  );
};

// ── GIF overlay sticker ─────────────────────────────────────────────────────

const GifOverlayView: React.FC<{
  src: string;
  durationInFrames: number;
  size: number;
  rotateFrom: number;
}> = ({ src, durationInFrames, size, rotateFrom }) => {
  const frame = useCurrentFrame();

  const enterFrames = Math.min(12, Math.max(6, Math.floor(durationInFrames * 0.3)));
  const exitFrames  = Math.min(8, Math.max(3, Math.floor(durationInFrames * 0.25)));
  const exitStart   = Math.max(0, durationInFrames - exitFrames);

  // Snappy overshoot pop entrance
  const enter = interpolate(frame, [0, enterFrames], [0, 1], {
    easing: Easing.bezier(0.34, 1.56, 0.64, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const exit = interpolate(frame, [exitStart, durationInFrames], [0, 1], {
    easing: Easing.in(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const opacity = Math.max(0, enter - exit);
  const scale   = interpolate(enter, [0, 1], [0.55, 1]) - exit * 0.15;
  const rotate  = interpolate(enter, [0, 1], [rotateFrom, 0]);

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={{
        position: "absolute",
        top: GIF_CENTER_Y - size / 2,
        left: (VIDEO_WIDTH - size) / 2,
        width: size,
        height: size,
        opacity,
        transform: `scale(${scale}) rotate(${rotate}deg)`,
        filter: "drop-shadow(0 12px 32px rgba(0,0,0,0.55))",
      }}>
        <AnimatedImage
          src={_resolveSrc(src)}
          width={size}
          height={size}
          fit="contain"
          loopBehavior="loop"
          style={{
            borderRadius: 24,
            background: "transparent",
          }}
        />
      </div>
    </AbsoluteFill>
  );
};

// ── Main composition ────────────────────────────────────────────────────────

export const HippoShort: React.FC<ShortProps> = (props) => {
  const { fps } = useVideoConfig();
  const gifs = props.gifs ?? [];

  return (
    <AbsoluteFill style={{ backgroundColor: COLOR.bg }}>
      {/* Pre-rendered background OR React fallback */}
      {props.bgImageSrc ? (
        <Img
          src={_resolveSrc(props.bgImageSrc)}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: VIDEO_WIDTH,
            height: VIDEO_HEIGHT,
            objectFit: "cover",
          }}
        />
      ) : (
        <>
          <div style={{
            position: "absolute",
            top: LAYOUT.banner.y,
            left: 0,
            width: "100%",
            height: LAYOUT.banner.h,
            backgroundColor: COLOR.bannerBg,
          }} />
          <div style={{
            position: "absolute",
            top: LAYOUT.title.y,
            left: 0,
            width: "100%",
            height: LAYOUT.title.h,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            color: COLOR.accent,
            fontSize: 64,
            fontWeight: 700,
            textAlign: "center",
            padding: "0 60px",
            fontFamily: "Gmarket Sans TTF, Pretendard, sans-serif",
            textShadow: "2px 2px 0 rgba(0,0,0,0.6)",
          }}>
            {props.hook}
          </div>
          <div style={{
            position: "absolute",
            top: LAYOUT.hash.y,
            left: 0,
            width: "100%",
            height: LAYOUT.hash.h,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            color: COLOR.hashtag,
            fontSize: 38,
            fontFamily: "Gmarket Sans TTF, Pretendard, sans-serif",
          }}>
            {props.hashtags}
          </div>
        </>
      )}

      {/* Clip zone */}
      <div style={{
        position: "absolute",
        top: LAYOUT.clip.y,
        left: 0,
        width: "100%",
        height: LAYOUT.clip.h,
        backgroundColor: "#000",
        overflow: "hidden",
      }}>
        {props.clipSrc && (
          <Video
            src={_resolveSrc(props.clipSrc)}
            muted
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        )}
      </div>

      {/* GIF overlays — center stickers floating above clip, below subtitles */}
      {gifs.map((gif, i) => {
        const startFrame = Math.round(gif.start * fps);
        const dur        = Math.max(1, Math.round(gif.duration * fps));
        return (
          <Sequence
            key={`gif-${i}-${gif.start}`}
            from={startFrame}
            durationInFrames={dur}
            layout="none"
          >
            <GifOverlayView
              src={gif.src}
              durationInFrames={dur}
              size={gif.size ?? 600}
              rotateFrom={gif.rotate ?? -6}
            />
          </Sequence>
        );
      })}

      {/* Subtitle chunks — each in its own Sequence with entrance animation */}
      {props.subtitles.map((sub, i) => {
        const next       = props.subtitles[i + 1];
        const startFrame = Math.round(sub.start * fps);
        const endTime    = next ? next.start : sub.end;
        const dur        = Math.max(1, Math.round((endTime - sub.start) * fps));
        return (
          <Sequence
            key={`sub-${i}-${sub.start}`}
            from={startFrame}
            durationInFrames={dur}
            layout="none"
          >
            <SubtitleChunkView text={sub.text} durationInFrames={dur} />
          </Sequence>
        );
      })}

      {/* Audio */}
      {props.ttsSrc && <Audio src={_resolveSrc(props.ttsSrc)} volume={props.ttsVolume} />}
      {props.bgmSrc && <Audio src={_resolveSrc(props.bgmSrc)} volume={props.bgmVolume} />}
    </AbsoluteFill>
  );
};
