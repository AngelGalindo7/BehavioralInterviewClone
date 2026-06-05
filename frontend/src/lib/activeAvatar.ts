import type { AvatarProvider } from "./avatarProvider";
import { createHeyGenProvider } from "./heygenAvatar";
import { createSimliProvider } from "./simliAvatar";

// "heygen" is the active provider. "simli" is opt-in and not maintained —
// set AVATAR_PROVIDER=simli on the backend to re-enable it.
export type AvatarProviderName = "simli" | "heygen";

export function getAvatarProvider(name: AvatarProviderName): AvatarProvider {
  switch (name) {
    case "simli":
      return createSimliProvider();
    case "heygen":
      return createHeyGenProvider();
    default: {
      const _exhaustive: never = name;
      throw new Error(`Unknown avatar provider: ${_exhaustive as string}`);
    }
  }
}
