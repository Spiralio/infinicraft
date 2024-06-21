#!env python3

from base64 import b64encode
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import struct
from rembg import remove as remove_bg
import urllib.parse
from PIL import Image
import numpy as np
from platform import system
from diffusers import StableDiffusionPipeline
import sys

print("Loading SD...")
pipeline = StableDiffusionPipeline.from_pretrained("runwayml/stable-diffusion-v1-5", use_safetensors=True)

if system() == "Darwin": 
    BACKEND = "MPS"
    print(f"Using Metal Performance Shaders (MPS) backend on platform {system()}...")
    pipeline.to("mps") # Mac uses MPS (Metal Performance Shaders) backend
    pipeline.enable_attention_slicing()
else:
    BACKEND = "CUDA"
    print(f"Using CUDA backend on platform {system()}...")
    pipeline.to("cuda") # Windows/Linux uses CUDA backend
    pipeline.enable_xformers_memory_efficient_attention()

print("Loaded SD, loading LoRA...")
try:
    pipeline.load_lora_weights("./models/",weight_name="lora.safetensors")
    print("LoRA loaded from local lora.safetensors.")
except:
    pipeline.load_lora_weights("OVAWARE/plixel-minecraft",weight_name="Plixel-SD-1.5.safetensors")
    print("LoRA loaded from Plixel-SD-1.5.safetensors")

pipeline.safety_checker = lambda i, **_: (i, [False] * len(i))
print("All Models loaded.")

# Caches
texture_cache = []


def downsample(image: Image) -> Image:
    pixels = np.array(image.getdata()).reshape((image.size[1], image.size[0]))
    return Image.fromarray(
        np.array([[pixels[16 * i + 8][16 * j + 8] for j in range(16)] for i in range(16)])
    )


def texture(item_description: str, debug=False) -> list[int] | Image:
    print('Requesting texture for:', item_description)
    im = pipeline(
        "Minecraft item, " + item_description + " white background.", 
        guidance_scale=8, 
        width=256, 
        height=256, 
        num_inference_steps=20
    ).images[0]
    im = remove_bg(im)
    im = downsample(im).convert("RGBA")
    if debug: return im
    texture: list[int] = []
    for y in range(16):
        for x in range(16):
            red,green,blue,alpha = im.getpixel((x,y))
            if alpha < 10:
                texture.append(-1)
                continue
            rgb = red
            rgb = (rgb << 8) + green
            rgb = (rgb << 8) + blue
            texture.append(rgb)
    return texture


class HttpRequestHandler(BaseHTTPRequestHandler):
    def send_fail(self, rs, p=None):
        print(f"Response: {rs}")
        if p is not None: print(p)
        self.send_response(rs)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"success": False}).encode('utf-8'))

    def send_success(self, content, content_type="application/json; charset=utf-8"):
        self.send_header("Content-Type", content_type)
        self.end_headers()
        if content is not None: self.wfile.write(content)

    def do_GET(self):
        """Serve a GET request."""
        print(f"Serving GET request...")
        url = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(url.query)

        if url.path not in ['/generate', '/generate-debug']: return self.send_fail(HTTPStatus.NOT_FOUND, f"url.path == {url.path}")

        item_description = qs.get('itemDescription', None)
        if item_description is None: return self.send_fail(HTTPStatus.BAD_REQUEST, "item_description is None")

        try:
            texture_result = texture(item_description[0])
        except Exception as err:
            return self.send_fail(HTTPStatus.INTERNAL_SERVER_ERROR, err)

        if url.path == '/generate_debug':
            self.send_success(None, "image/png")
            texture_result.save(self.wfile, 'png')

        return self.send_success(
            json.dumps(
                {
                    "success": True, 
                    "image": b64encode(
                        struct.pack('<{}i'.format(len(texture_result)), *texture_result)
                    ).decode('utf-8'),
                }
            ).encode('utf-8')
        )


if __name__ == "__main__":
    httpd = ThreadingHTTPServer(('', 17707), HttpRequestHandler)
    print("Server running on port 17707")
    httpd.serve_forever()
