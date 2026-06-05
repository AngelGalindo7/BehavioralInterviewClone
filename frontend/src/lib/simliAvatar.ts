// Not maintained — Simli is opt-in only. HeyGen/LiveAvatar is the active avatar.
// To re-enable: set SIMLI_API_KEY + SIMLI_FACE_ID + AVATAR_PROVIDER=simli on the
// backend. Re-validate PCM pacing and _drain_and_pace before production use.
//
// simli-client v3 API notes (differs significantly from older versions):
//   - Named export { SimliClient } (not default)
//   - Positional constructor: (session_token, video, audio, iceServers, ...)
//   - No Initialize() method — config goes via constructor
//   - ClearBuffer() replaces the old interrupt()
//   - sendAudioDataImmediate() bypasses the SDK's jitter buffer (use for the
//     first chunk of each utterance so lip-sync starts immediately)
//   - sendAudioData() is the buffered ingest path
//   - stop() (not close()) tears down the WebRTC session

import { SimliClient } from "simli-client";
import type { AvatarProvider, AvatarInitParams } from "./avatarProvider";

export function createSimliProvider(): AvatarProvider {
  let client: SimliClient | null = null;
  let _simliSendCount = 0;

  return {
    async init(params: AvatarInitParams): Promise<void> {
      _simliSendCount = 0;
      client = new SimliClient(
        params.sessionToken,
        params.videoEl,
        params.audioEl,
        params.iceServers,
      );
      console.log("[SIMLI] init: calling client.start()");
      await client.start();
      console.log("[SIMLI] init: client.start() resolved — WebRTC session up");
    },

    sendAudio(pcm: Uint8Array, immediate: boolean): void {
      if (!client) {
        console.error("[SIMLI] sendAudio called but client is null — audio dropped");
        return;
      }
      const isAligned = pcm.byteLength % 2 === 0;
      if (!isAligned) {
        console.error(
          `[SIMLI] MISALIGNED PCM passed to avatar send #${_simliSendCount}: byteLength=${pcm.byteLength} (odd) — static expected`
        );
      } else {
        console.debug(
          `[SIMLI] send #${_simliSendCount}: byteLength=${pcm.byteLength} method=${immediate ? "sendAudioDataImmediate" : "sendAudioData"}`
        );
      }
      _simliSendCount++;
      if (immediate) {
        client.sendAudioDataImmediate(pcm);
      } else {
        client.sendAudioData(pcm);
      }
    },

    interrupt(): void {
      console.log("[SIMLI] interrupt: calling ClearBuffer()");
      client?.ClearBuffer();
    },

    async destroy(): Promise<void> {
      if (!client) return;
      console.log("[SIMLI] destroy: calling client.stop()");
      try {
        await client.stop();
      } finally {
        client = null;
        _simliSendCount = 0;
      }
    },
  };
}
