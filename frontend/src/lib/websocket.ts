import { adminWsUrl, type AdminWsEvent } from "./api";

export interface AdminSocket {
  close: () => void;
}

export function connectAdminSocket(options: {
  token: string;
  onOpen: () => void;
  onClose: () => void;
  onEvent: (event: AdminWsEvent) => void;
}): AdminSocket {
  const socket = new WebSocket(adminWsUrl(options.token));
  socket.addEventListener("open", options.onOpen);
  socket.addEventListener("close", options.onClose);
  socket.addEventListener("error", options.onClose);
  socket.addEventListener("message", (message: MessageEvent<string>) => {
    try {
      options.onEvent(JSON.parse(message.data) as AdminWsEvent);
    } catch {
      return;
    }
  });
  return {
    close: () => socket.close()
  };
}
