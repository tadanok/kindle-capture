#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <Vision/Vision.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) {
            fprintf(stderr, "usage: kindle-vision-ocr IMAGE\n");
            return 2;
        }

        NSString *path = [NSString stringWithUTF8String:argv[1]];
        NSURL *url = [NSURL fileURLWithPath:path];
        CGImageSourceRef source = CGImageSourceCreateWithURL(
            (__bridge CFURLRef)url,
            NULL
        );
        if (!source) {
            fprintf(stderr, "failed to open image\n");
            return 1;
        }
        CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, NULL);
        CFRelease(source);
        if (!image) {
            fprintf(stderr, "failed to decode image\n");
            return 1;
        }

        VNRecognizeTextRequest *request =
            [[VNRecognizeTextRequest alloc] initWithCompletionHandler:nil];
        request.revision = VNRecognizeTextRequestRevision3;
        request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        request.usesLanguageCorrection = YES;
        request.recognitionLanguages = @[@"ja-JP", @"en-US"];
        request.minimumTextHeight = 0.008;

        VNImageRequestHandler *handler =
            [[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
        NSError *error = nil;
        BOOL succeeded = [handler performRequests:@[request] error:&error];
        CGImageRelease(image);
        if (!succeeded) {
            const char *message = error
                ? error.localizedDescription.UTF8String
                : "unknown Vision error";
            fprintf(stderr, "Vision OCR failed: %s\n", message);
            return 1;
        }

        for (VNRecognizedTextObservation *observation in request.results) {
            VNRecognizedText *candidate =
                [observation topCandidates:1].firstObject;
            if (!candidate) {
                continue;
            }
            NSString *text = [candidate.string
                stringByReplacingOccurrencesOfString:@"\t"
                withString:@" "];
            text = [text
                stringByReplacingOccurrencesOfString:@"\n"
                withString:@" "];
            CGRect box = observation.boundingBox;
            printf(
                "%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%s\n",
                box.origin.x,
                box.origin.y,
                box.size.width,
                box.size.height,
                candidate.confidence,
                text.UTF8String
            );
        }
    }
    return 0;
}
