export interface AvatarInitParams {
  sessionToken: string;
  iceServers: RTCIceServer[];
  videoEl: HTMLVideoElement;
  audioEl: HTMLAudioElement;
  // LiveKit URL — set only for providers that join a LiveKit room (HeyGen).
  // Simli ignores this and negotiates its own WebRTC via simli-client.
  url?: string;
}

export interface AvatarProvider {
  init(params: AvatarInitParams): Promise<void>;
  sendAudio(pcm: Uint8Array, immediate: boolean): void;
  interrupt(): void;
  destroy(): Promise<void>;
}
