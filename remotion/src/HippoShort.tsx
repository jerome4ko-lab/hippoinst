import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  Video,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export const FPS = 30;
export const VIDEO_WIDTH = 1080;
export const VIDEO_HEIGHT = 1920;

export type SubtitleChunk = {
  text: string;
  start: number;
  end: number;
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
  durationInSeconds: 55,
  subtitles: [
    { text: "이 로봇은",       start: 0.0, end: 1.5 },
    { text: "단 하루 만에",     start: 1.5, end: 3.0 },
    { text: "작업을 학습합니다", start: 3.0, end: 5.0 },
  ],
};

export const HippoShort: React.FC<ShortProps> = (props) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;

  const activeSub = props.subtitles.find((s) => t >= s.start && t < s.end);

  return (
    <AbsoluteFill style={{ backgroundColor: COLOR.bg }}>
      {/* Banner zone — pre-rendered PNG OR fallback color block */}
      {props.bgImageSrc ? (
        <Img
          src={props.bgImageSrc}
          style={{
            position: "absolute",
            top: 0, left: 0,
            width: VIDEO_WIDTH, height: VIDEO_HEIGHT,
            objectFit: "cover",
          }}
        />
      ) : (
        <>
          <div style={{
            position: "absolute",
            top: LAYOUT.banner.y, left: 0,
            width: "100%", height: LAYOUT.banner.h,
            backgroundColor: COLOR.bannerBg,
          }} />
          <div style={{
            position: "absolute",
            top: LAYOUT.title.y, left: 0,
            width: "100%", height: LAYOUT.title.h,
            display: "flex", justifyContent: "center", alignItems: "center",
            color: COLOR.accent, fontSize: 64, fontWeight: 700,
            textAlign: "center", padding: "0 60px",
            fontFamily: "Gmarket Sans TTF, Pretendard, sans-serif",
            textShadow: "2px 2px 0 rgba(0,0,0,0.6)",
          }}>
            {props.hook}
          </div>
          <div style={{
            position: "absolute",
            top: LAYOUT.hash.y, left: 0,
            width: "100%", height: LAYOUT.hash.h,
            display: "flex", justifyContent: "center", alignItems: "center",
            color: COLOR.hashtag, fontSize: 38,
            fontFamily: "Gmarket Sans TTF, Pretendard, sans-serif",
          }}>
            {props.hashtags}
          </div>
        </>
      )}

      {/* Clip zone */}
      <div style={{
        position: "absolute",
        top: LAYOUT.clip.y, left: 0,
        width: "100%", height: LAYOUT.clip.h,
        backgroundColor: "#000",
        overflow: "hidden",
      }}>
        {props.clipSrc && (
          <Video
            src={props.clipSrc}
            muted
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        )}
      </div>

      {/* Subtitle chunk — placed near bottom of clip zone */}
      {activeSub && (
        <div style={{
          position: "absolute",
          left: 0, right: 0,
          top: LAYOUT.clip.y + LAYOUT.clip.h - 140,
          display: "flex", justifyContent: "center", alignItems: "center",
          padding: "0 60px",
        }}>
          <span style={{
            color: COLOR.accent,
            fontSize: 78,
            fontWeight: 700,
            textAlign: "center",
            textShadow: "0 4px 12px rgba(0,0,0,0.95), 0 0 4px rgba(0,0,0,0.95)",
            fontFamily: "Gmarket Sans TTF, Pretendard, sans-serif",
            letterSpacing: -1,
          }}>
            {activeSub.text}
          </span>
        </div>
      )}

      {/* Audio */}
      {props.ttsSrc && (
        <Audio src={props.ttsSrc} volume={props.ttsVolume} />
      )}
      {props.bgmSrc && (
        <Audio src={props.bgmSrc} volume={props.bgmVolume} />
      )}
    </AbsoluteFill>
  );
};
