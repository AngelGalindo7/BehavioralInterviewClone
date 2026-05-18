import type { AvatarProvider } from "./avatarProvider";
import { createHeyGenProvider } from "./heygenAvatar";
import { createSimliProvider } from "./simliAvatar";

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
