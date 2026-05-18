export interface AvatarInitParams {
  sessionToken: string;
  iceServers: RTCIceServer[];
  videoEl: HTMLVideoElement;
  audioEl: HTMLAudioElement;
}

export interface AvatarProvider {
  init(params: AvatarInitParams): Promise<void>;
  sendAudio(pcm: Uint8Array, immediate: boolean): void;
  interrupt(): void;
  destroy(): Promise<void>;
}
