/* Scalar, naturally aligned AXI reads and lossless MJPEG packet muxing. */
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <libavformat/avformat.h>
#include <libavutil/mathematics.h>

void copy_device_words(const volatile uint32_t *source, volatile uint32_t *dest, size_t words)
{
    for (size_t i = 0; i < words; ++i)
        dest[i] = source[i];
}

typedef struct {
    AVFormatContext *format;
    AVStream *video;
    AVRational period;
    int64_t frame;
} JpegMovie;

static int release_movie(JpegMovie *movie)
{
    int err = 0;
    if (movie->format) {
        if (movie->format->pb)
            err = avio_closep(&movie->format->pb);
        avformat_free_context(movie->format);
    }
    av_free(movie);
    return err;
}

int movie_open(JpegMovie **out, const char *path, double fps, int width, int height)
{
    JpegMovie *movie = av_mallocz(sizeof(*movie));
    if (!movie)
        return AVERROR(ENOMEM);
    int err = avformat_alloc_output_context2(&movie->format, NULL, "mp4", path);
    if (err < 0)
        goto fail;
    AVStream *video = movie->video = avformat_new_stream(movie->format, NULL);
    if (!video) {
        err = AVERROR(ENOMEM);
        goto fail;
    }
    video->avg_frame_rate = av_d2q(fps, 100000);
    movie->period = video->time_base = av_inv_q(video->avg_frame_rate);
    video->codecpar->codec_type = AVMEDIA_TYPE_VIDEO;
    video->codecpar->codec_id = AV_CODEC_ID_MJPEG;
    video->codecpar->width = width;
    video->codecpar->height = height;
    err = avio_open(&movie->format->pb, path, AVIO_FLAG_WRITE);
    if (err < 0)
        goto fail;
    err = avformat_write_header(movie->format, NULL);
    if (err < 0)
        goto fail;
    *out = movie;
    return 0;
fail:
    release_movie(movie);
    return err;
}

int movie_write(JpegMovie *movie, const uint8_t *jpeg, int size)
{
    /* Single stream, synchronous write. The caller owns the JPEG until return. */
    AVPacket *packet = av_packet_alloc();
    if (!packet)
        return AVERROR(ENOMEM);
    packet->data = (uint8_t *)jpeg;
    packet->size = size;
    packet->stream_index = movie->video->index;
    packet->flags = AV_PKT_FLAG_KEY;
    packet->pts = packet->dts = av_rescale_q(movie->frame, movie->period, movie->video->time_base);
    packet->duration = av_rescale_q(movie->frame + 1, movie->period, movie->video->time_base) - packet->pts;
    int err = av_write_frame(movie->format, packet);
    av_packet_free(&packet);
    if (err >= 0)
        movie->frame++;
    return err;
}

int movie_close(JpegMovie *movie)
{
    int trailer = av_write_trailer(movie->format);
    int closed = release_movie(movie);
    return trailer < 0 ? trailer : closed;
}
