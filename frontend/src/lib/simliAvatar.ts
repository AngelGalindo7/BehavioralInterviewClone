/**
 * Simli WebRTC avatar lifecycle manager (simli-client v3.x).
 *
 * v3 API differs significantly from older versions:
 *   - Named export `{ SimliClient }` (not default)
 *   - Positional constructor: (session_token, video, audio, iceServers, ...)
 *   - No `Initialize()` method — config goes via constructor
 *   - `ClearBuffer()` replaces the old `interrupt()`
 *   - `sendAudioDataImmediate()` bypasses the SDK's jitter buffer (use for the
 *     first chunk of each utterance so lip-sync starts immediately)
 *   - `sendAudioData()` is the buffered ingest path
 *   - `stop()` (not `close()`) tears down the WebRTC session
 *
 * The backend prepends one byte to every PCM frame to tell us which method to
 * call (see app/avatar/simli_client.py): 0x01 → immediate, 0x00 → buffered.
 */

import { SimliClient } from "simli-client";

let client: SimliClient | null = null;
let _simliSendCount = 0;

export interface AvatarInitParams {
  sessionToken: string;
  iceServers: RTCIceServer[];
  videoEl: HTMLVideoElement;
  audioEl: HTMLAudioElement;
}

export async function initSimliAvatar(params: AvatarInitParams): Promise<void> {
  _simliSendCount = 0;
  client = new SimliClient(
    params.sessionToken,
    params.videoEl,
    params.audioEl,
    params.iceServers,
  );
  console.log("[SIMLI] initSimliAvatar: calling client.start()");
  await client.start();
  console.log("[SIMLI] initSimliAvatar: client.start() resolved — WebRTC session up");
}

export function sendAudioToAvatar(pcm: Uint8Array, immediate: boolean): void {
  if (!client) {
    console.error("[SIMLI] sendAudioToAvatar called but client is null — audio dropped");
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
}

export function interruptAvatar(): void {
  console.log("[SIMLI] interruptAvatar: calling ClearBuffer()");
  client?.ClearBuffer();
}

export async function destroyAvatar(): Promise<void> {
  if (!client) return;
  console.log("[SIMLI] destroyAvatar: calling client.stop()");
  try {
    await client.stop();
  } finally {
    client = null;
    _simliSendCount = 0;
  }
}
