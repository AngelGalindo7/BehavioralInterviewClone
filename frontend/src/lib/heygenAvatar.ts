/**
 * LiveAvatar LITE frontend adapter — LiveKit-based.
 *
 * The "heygen" name is kept across the codebase for backwards compat (env vars,
 * the toggle button, deps.py registration); HeyGen v1 streaming was sunset
 * 2026-03-31 and the backend now talks to LiveAvatar (HeyGen's successor).
 *
 * Frontend ONLY joins the LiveKit room — `params.url` is the LiveKit URL
 * (livekit_url from POST /v1/sessions/start) and `params.sessionToken` is the
 * room access token (livekit_client_token). Backend handles session creation,
 * opens its own WebSocket to LiveAvatar, and forwards ElevenLabs PCM (24 kHz)
 * as `agent.speak` events. The avatar lip-syncs and publishes video/audio
 * tracks into this room, which we attach to AvatarView's <video>/<audio>.
 *
 * `sendAudio` is intentionally a no-op: PCM never flows through this provider
 * (mode is audio_pcm_server — backend pushes upstream, not via browser).
 * `interrupt` is a no-op pending a backend control channel for avatar.interrupt.
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
      // audio_pcm_server provider: backend forwards PCM to LiveAvatar's WS
      // directly; the browser only consumes the LiveKit room.
    },

    interrupt(): void {
      // Skip parity with Simli requires the backend to send an avatar.interrupt
      // event over its LiveAvatar WS. Deferred until that control path is wired.
      console.log("[HEYGEN] interrupt: no-op (backend interrupt path not wired)");
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
