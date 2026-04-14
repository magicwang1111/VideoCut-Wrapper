import OSS from 'ali-oss';
import fs from 'node:fs';
import path from 'node:path';

export class OssClient {
  private client: OSS;
  private bucket: string;
  private prefix: string;

  constructor() {
    this.bucket = process.env.OSS_BUCKET ?? 'goumee-coze';
    this.prefix = process.env.OSS_PREFIX ?? 'GouMei-Video-Cut';
    this.client = new OSS({
      endpoint: process.env.OSS_ENDPOINT ?? 'oss-cn-hangzhou-internal.aliyuncs.com',
      accessKeyId: process.env.OSS_ACCESS_KEY_ID!,
      accessKeySecret: process.env.OSS_ACCESS_KEY_SECRET!,
      bucket: this.bucket,
    });
  }

  inputKey(fileId: string, ext: string): string {
    return `${this.prefix}/inputs/${fileId}${ext}`;
  }

  outputKey(taskId: string): string {
    return `${this.prefix}/outputs/${taskId}/final.mp4`;
  }

  async upload(localPath: string, ossKey: string): Promise<void> {
    await this.client.put(ossKey, localPath);
  }

  async download(ossKey: string, localPath: string): Promise<void> {
    fs.mkdirSync(path.dirname(localPath), { recursive: true });
    const result = await this.client.getStream(ossKey);
    await new Promise<void>((resolve, reject) => {
      const out = fs.createWriteStream(localPath);
      result.stream.pipe(out);
      out.on('finish', resolve);
      out.on('error', reject);
    });
  }

  presignUrl(ossKey: string, expiresSeconds = 3600): string {
    return this.client.signatureUrl(ossKey, { expires: expiresSeconds });
  }
}
