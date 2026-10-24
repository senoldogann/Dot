// The audio worklet of the composer's recording (item 626, capture.js adds it to the page's audio context): it runs on
// the audio thread, takes the samples the microphone gives it, 128 at a time, and hands the page a block of them, as one
// array of Float32 from -1 to 1, each time `BLOCK` have come in: some 40 milliseconds, often enough for the wave of the
// recorder and for telling a silence, and few enough messages for a phone. It plays nothing and keeps nothing.

// How many samples go to the page in one block.
const BLOCK = 2048;

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = new Float32Array(BLOCK);
    this.filled = 0;
  }

  /** @param {Float32Array[][]} inputs */
  process(inputs) {
    const channels = inputs[0];
    if (channels === undefined || channels.length === 0) {
      return true;
    }
    const frames = channels[0].length;
    for (let frame = 0; frame < frames; frame += 1) {
      // A microphone that gives two channels is heard as one: their mean.
      let sample = 0;
      for (const channel of channels) {
        sample += channel[frame];
      }
      this.block[this.filled] = sample / channels.length;
      this.filled += 1;
      if (this.filled === BLOCK) {
        this.port.postMessage(this.block, [this.block.buffer]);
        this.block = new Float32Array(BLOCK);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("dot-capture", CaptureProcessor);
