import { Composition } from "remotion";
import { FullEpisode } from "./FullEpisode";
import manifest from "./remotion_manifest.json";

export const RemotionRoot: React.FC = () => {
  const FPS = 30;
  
  // Calculate total frames dynamically from the last scene's end time
  const lastScene = manifest[manifest.length - 1];
  const totalFrames = Math.ceil(lastScene.endSec * FPS) + (2 * FPS); 

  return (
    <Composition
      id="YourTechLab-Episode"
      component={FullEpisode}
      durationInFrames={totalFrames} 
      fps={FPS}
      width={1920}
      height={1080}
      defaultProps={{
        hasBackgroundMusic: false, 
      }}
    />
  );
};
