import requests
import re
import json
import sys
import pickle
import os.path
import subprocess
import urllib.parse
from typing import Optional, Dict, Any, List

class XVideosScraper:

    def __init__(self, cookies_path: Optional[str] = None):
        self.cookies_path = cookies_path
        self.session = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        self.proxies = {
            'http': None,
            'https': None,
        }
        
        self.video_regex = re.compile(r'''(?x)
                    https?://
                        (?:
                            (?:[^/]+\.)?xvideos2?\.com/video\.?|
                            (?:www\.)?xvideos\.es/video\.?|
                            (?:www|flashservice)\.xvideos\.com/embedframe/|
                            static-hw\.xvideos\.com/swf/xv-player\.swf\?.*?\bid_video=
                        )
                        (?P<id>[0-9a-z]+)
                    ''')

        if self.cookies_path and os.path.isfile(self.cookies_path):
            self.load_cookies()

    def set_proxies(self, http_proxy: str, https_proxy: str) -> None:
        self.proxies['http'] = http_proxy
        self.proxies['https'] = https_proxy

    def load_cookies(self) -> bool:
        try:
            with open(self.cookies_path, 'rb') as f:
                cookies = pickle.load(f)
                self.session.cookies.update(cookies)
            return True
        except Exception as e:
            print(f"Error loading cookies: {e}")
            return False

    def download_webpage(self, url: str) -> str:
        try:
            response = self.session.get(url, headers=self.headers, proxies=self.proxies, timeout=10)
            response.raise_for_status()
            return response.text
        except Exception as e:
            raise Exception(f'Error downloading page: {e}')

    def extract_title(self, webpage: str) -> str:
        title = None
        m_title = re.search(r'<title>(?P<title>.+?)\s+-\s+XVID', webpage, re.IGNORECASE)
        if m_title:
            title = m_title.group('title')
        
        if not title:
            m_title = re.search(r'setVideoTitle\s*\(\s*(["\'])(?P<title>(?:(?!\1).)+)\1', webpage)
            if m_title:
                title = m_title.group('title')
        
        if not title:
            title = 'XVideos_video'
        
        return title.strip()

    def extract_thumbnails(self, webpage: str) -> List[Dict[str, Any]]:
        thumbnails = []
        for pref, suffix in enumerate(('', '169')):
            pattern = rf'setThumbUrl{suffix}\(\s*(["\'])(?P<thumbnail>(?:(?!\1).)+)\1'
            m_thumb = re.search(pattern, webpage)
            if m_thumb:
                thumbnail_url = m_thumb.group('thumbnail')
                thumbnails.append({
                    'url': thumbnail_url,
                    'preference': pref,
                })
        return thumbnails

    def extract_duration(self, webpage: str) -> Optional[int]:
        m_duration = re.search(r'<span[^>]+class=["\']duration["\'][^>]*>.*?(\d[^<]+)', webpage, re.IGNORECASE)
        if not m_duration:
            return None
        
        duration_str = m_duration.group(1).strip()
        parts = duration_str.split(':')
        
        try:
            parts = list(map(int, parts))
            if len(parts) == 2:
                minutes, seconds = parts
                return minutes * 60 + seconds
            elif len(parts) == 3:
                hours, minutes, seconds = parts
                return hours * 3600 + minutes * 60 + seconds
        except Exception:
            return None
        
        return None

    def determine_ext(self, url: str) -> str:
        m_ext = re.search(r'\.([a-z0-9]+)(?:\?.+)?$', url, re.IGNORECASE)
        return m_ext.group(1).lower() if m_ext else 'mp4'

    def extract_m3u8_formats(self, hls_url: str, video_id: str) -> List[Dict[str, Any]]:
        formats = []
        
        try:
            response = self.session.get(hls_url, headers=self.headers, proxies=self.proxies, timeout=10)
            response.raise_for_status()
            playlist = response.text
        except Exception as e:
            print(f"Error downloading m3u8 playlist: {e}")
            return formats

        if "#EXT-X-STREAM-INF" not in playlist:
            formats.append({
                'url': hls_url,
                'format_id': 'hls',
                'ext': 'mp4'
            })
            return formats

        lines = playlist.splitlines()
        stream_info = {}
        
        for line in lines:
            line = line.strip()
            
            if line.startswith("#EXT-X-STREAM-INF"):
                stream_info = {}
                bw_match = re.search(r'BANDWIDTH=(\d+)', line)
                res_match = re.search(r'RESOLUTION=(\d+x\d+)', line)
                
                if bw_match:
                    stream_info['bandwidth'] = int(bw_match.group(1))
                if res_match:
                    stream_info['resolution'] = res_match.group(1)
                    
            elif line and not line.startswith("#"):
                format_dict = {
                    'url': urllib.parse.urljoin(hls_url, line),
                    'format_id': 'hls',
                    'ext': 'mp4',
                }
                
                if 'resolution' in stream_info:
                    format_dict['format_id'] = f"hls-{stream_info['resolution']}"
                    width, height = stream_info['resolution'].split('x')
                    format_dict['width'] = int(width)
                    format_dict['height'] = int(height)
                
                if 'bandwidth' in stream_info:
                    format_dict['bandwidth'] = stream_info['bandwidth']
                
                formats.append(format_dict)
                stream_info = {}
        
        return formats

    def extract_formats(self, webpage: str, video_id: str) -> List[Dict[str, Any]]:
        formats = []

        m_flv = re.search(r'flv_url=(.+?)&', webpage)
        if m_flv:
            video_url = urllib.parse.unquote(m_flv.group(1))
            formats.append({
                'url': video_url,
                'format_id': 'flv'
            })

        matches = re.findall(r'setVideo([^(]+)\((["\'])(http.+?)\2\)', webpage)
        
        for kind, _, format_url in matches:
            format_id = kind.lower().strip()
            
            if format_id == 'hls':
                hls_formats = self.extract_m3u8_formats(format_url, video_id)
                formats.extend(hls_formats)
            elif format_id in ('urllow', 'urlhigh'):
                quality = -2 if format_id.endswith('low') else None
                ext = self.determine_ext(format_url)
                formats.append({
                    'url': format_url,
                    'format_id': f'{ext}-{format_id[3:]}',
                    'quality': quality
                })
        
        return formats

    def extract_video_info(self, url: str) -> Dict[str, Any]:
        m = self.video_regex.match(url)
        if not m:
            raise Exception("Invalid URL for XVideos.")
        
        video_id = m.group('id')
        webpage = self.download_webpage(url)

        m_error = re.search(r'<h1\s+class=["\']inlineError["\']>(.+?)</h1>', webpage, re.IGNORECASE)
        if m_error:
            error_msg = re.sub(r'\s+', ' ', m_error.group(1)).strip()
            raise Exception(f"XVideos reports: {error_msg}")

        title = f"DescargarBot_XVideos_{video_id}"
        thumbnails = self.extract_thumbnails(webpage)
        duration = self.extract_duration(webpage)
        formats = self.extract_formats(webpage, video_id)

        return {
            'id': video_id,
            'title': title,
            'duration': duration,
            'thumbnails': thumbnails,
            'formats': formats
        }

    def get_quality_value(self, fmt: Dict[str, Any]) -> int:
        if 'width' in fmt and 'height' in fmt:
            return fmt['width'] * fmt['height']
        elif 'bandwidth' in fmt:
            return fmt['bandwidth']
        else:
            return 0

    def get_best_format(self, formats: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not formats:
            return None

        best_format = max(formats, key=self.get_quality_value)
        return best_format

    def download_video_with_ffmpeg(self, video_url: str, output_video: str) -> bool:
        try:
            cmd = [
                'ffmpeg',
                '-user_agent', self.headers['User-Agent'],
                '-i', video_url,
                '-c', 'copy',
                '-bsf:a', 'aac_adtstoasc',
                output_video
            ]
            
            print("Downloading video with ffmpeg...")
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"Video downloaded successfully: {output_video}")
            
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error executing ffmpeg: {e}")
            print(f"stderr: {e.stderr}")
            return False
        except Exception as e:
            print(f"Unexpected error: {e}")
            return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("You must provide the URL of an XVideos video")
        sys.exit(1)

    video_url = sys.argv[1]
    cookies_path = sys.argv[2] if len(sys.argv) > 2 else None
    scraper = XVideosScraper(cookies_path=cookies_path)

    try:
        video_info = scraper.extract_video_info(video_url)
        print("Video information extracted:")
        print(json.dumps(video_info, indent=4))

        best = scraper.get_best_format(video_info.get('formats', []))
        if best:
            print(f"\nBest format found: {best.get('format_id')} - {best.get('url')}")
            
            output_video = f"{video_info['title']}.mp4"
            output_video = re.sub(r'[<>:"/\\|?*]', '_', output_video)
            scraper.download_video_with_ffmpeg(best['url'], output_video)
        else:
            print("No available formats found.")
    except Exception as e:
        print(f"Error: {e}")
      
