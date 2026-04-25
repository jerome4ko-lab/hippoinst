import React from "react";
import { Composition } from "remotion";
import { HippoShort, defaultProps, FPS, VIDEO_WIDTH, VIDEO_HEIGHT } from "./HippoShort";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="HippoShort"
        component={HippoShort}
        defaultProps={defaultProps}
        fps={FPS}
        width={VIDEO_WIDTH}
        height={VIDEO_HEIGHT}
        durationInFrames={Math.round(defaultProps.durationInSeconds * FPS)}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.max(1, Math.round(props.durationInSeconds * FPS)),
        })}
      />
    </>
  );
};
