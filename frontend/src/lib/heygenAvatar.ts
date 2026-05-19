/**
 * HeyGen Streaming Avatar V2 frontend adapter — LiveKit-based.
 *
 * Frontend ONLY joins the LiveKit room. Session creation, `streaming.start`,
 * and `streaming.task` (speak) all run on the backend — keeps the HeyGen API
 * key server-side. Backend POSTs text per sentence flush; HeyGen runs TTS +
 * lip-sync server-side and publishes the resulting video/audio tracks into
 * the room, which we attach to the existing AvatarView <video>/<audio> refs.
 *
 * `sendAudio` is intentionally a no-op: PCM never flows through this provider
 * (text-mode, see app/avatar/base.py). `interrupt` is a no-op pending a
 * backend interrupt endpoint to call streaming.task with task_type=interrupt.
 */

import { Room, RoomEvent } from "livekit-client";
import type { AvatarProvider, AvatarInitParams } from "./avatarProvider";

export function createHeyGenProvider(): AvatarProvider {
  let room: Room | null = null;

  return {
    async init(params: AvatarInitParams): Promise<void> {
      if (!params.url) {
        throw new Error("[HEYGEN] init requires AvatarInitParams.url (LiveKit URL)");
      }
      room = new Room({
        adaptiveStream: true,
        dynacast: true,
      });

      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === "video") {
          track.attach(params.videoEl);
          console.log("[HEYGEN] video track attached");
        } else if (track.kind === "audio") {
          track.attach(params.audioEl);
          console.log("[HEYGEN] audio track attached");
        }
      });

      console.log("[HEYGEN] init: connecting to LiveKit room");
      const connectOpts =
        params.iceServers && params.iceServers.length > 0
          ? { rtcConfig: { iceServers: params.iceServers as RTCIceServer[] } }
          : undefined;
      await room.connect(params.url, params.sessionToken, connectOpts);
      console.log("[HEYGEN] init: LiveKit connected, awaiting publisher tracks");
    },

    sendAudio(_pcm: Uint8Array, _immediate: boolean): void {
      // Text-mode provider: backend POSTs text to streaming.task; no PCM here.
    },

    interrupt(): void {
      // Skip parity with Simli requires a streaming.task interrupt call from
      // the backend. Deferred until that endpoint exists.
      console.log("[HEYGEN] interrupt: no-op (backend interrupt endpoint not wired)");
    },

    async destroy(): Promise<void> {
      if (!room) return;
      console.log("[HEYGEN] destroy: disconnecting LiveKit room");
      try {
        await room.disconnect();
      } finally {
        room = null;
      }
    },
  };
}
