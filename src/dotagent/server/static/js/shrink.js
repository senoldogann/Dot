// A picture made smaller before it is uploaded (item 990): a phone's photo or screenshot of several megabytes went whole,
// as base64, over a mobile network, and the message waited for it. A picture past SHRINK_FROM_BYTES whose longer side
// is past SHRINK_EDGE pixels goes as a JPEG of that side; a model reads a picture at about that size at most.

// The longer side a picture is brought down to, and the size from which it is.
export const SHRINK_EDGE = 2048;
export const SHRINK_FROM_BYTES = 1024 * 1024;
const QUALITY = 0.88;
// What a browser decodes everywhere; HEIC is not among them (Chrome on a Mac does not), and a GIF may move.
const SHRINKS = new Set(["image/jpeg", "image/png", "image/webp"]);

/** The size of a `width` × `height` picture brought within `edge` pixels on its longer side, its shape kept. */
export function fitWithin(width, height, edge) {
  const longer = Math.max(width, height);
  if (longer <= edge) {
    return { width, height };
  }
  const scale = edge / longer;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/** The name of a picture sent as a JPEG: its own, with `.jpg` for its extension. */
export function jpegName(name) {
  const dot = name.lastIndexOf(".");
  return `${dot > 0 ? name.slice(0, dot) : name}.jpg`;
}

/**
 * `file`, or a smaller JPEG of it when it is a picture past SHRINK_FROM_BYTES and SHRINK_EDGE pixels and the JPEG is
 * smaller. A transparent picture's clear parts turn white, as a JPEG has none. A picture the browser cannot read fails
 * its upload with the browser's error, on its chip.
 */
export async function smallerPicture(file) {
  if (!SHRINKS.has(file.type) || file.size < SHRINK_FROM_BYTES) {
    return file;
  }
  const bitmap = await createImageBitmap(file);
  try {
    const size = fitWithin(bitmap.width, bitmap.height, SHRINK_EDGE);
    // A picture within the edge goes as it is, whatever its weight: a screenshot's text stays sharp, and a PNG clear.
    if (size.width === bitmap.width && size.height === bitmap.height) {
      return file;
    }
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, size.width, size.height);
    context.drawImage(bitmap, 0, 0, size.width, size.height);
    const blob = await new Promise((resolve, reject) => {
      canvas.toBlob((made) => (made === null ? reject(new Error("the picture could not be made smaller")) : resolve(made)), "image/jpeg", QUALITY);
    });
    return blob.size < file.size ? new File([blob], jpegName(file.name), { type: "image/jpeg" }) : file;
  } finally {
    bitmap.close();
  }
}
