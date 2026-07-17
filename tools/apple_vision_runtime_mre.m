#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <ImageIO/ImageIO.h>
#import <CoreGraphics/CoreGraphics.h>

static CGImageRef loadImage(NSString *path) {
    NSURL *url = [NSURL fileURLWithPath:path];
    CGImageSourceRef source = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
    if (source == NULL) return NULL;
    CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, NULL);
    CFRelease(source);
    return image;
}

static void writeJSON(NSDictionary *payload) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:payload options:0 error:&error];
    if (data == nil) {
        fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
        return;
    }
    fwrite(data.bytes, 1, data.length, stdout);
    fputc('\n', stdout);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 3 || argc > 5) {
            fprintf(stderr, "usage: apple_vision_runtime_mre IMAGE accurate|fast [LANGUAGE|auto] [correction|no-correction]\n");
            return 2;
        }
        NSString *path = [NSString stringWithUTF8String:argv[1]];
        NSString *level = [NSString stringWithUTF8String:argv[2]];
        NSString *language = argc >= 4 ? [NSString stringWithUTF8String:argv[3]] : @"zh-Hant";
        BOOL correction = argc < 5 || strcmp(argv[4], "no-correction") != 0;
        CGImageRef image = loadImage(path);
        if (image == NULL) {
            writeJSON(@{@"image_loaded": @NO, @"image_path": path});
            return 3;
        }

        __block NSError *completionError = nil;
        VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc]
            initWithCompletionHandler:^(VNRequest *completedRequest, NSError *error) {
                completionError = error;
            }];
        request.revision = VNRecognizeTextRequestRevision3;
        request.recognitionLevel = [level isEqualToString:@"fast"]
            ? VNRequestTextRecognitionLevelFast
            : VNRequestTextRecognitionLevelAccurate;
        if (![language isEqualToString:@"auto"]) {
            request.recognitionLanguages = @[language];
        } else if ([request respondsToSelector:@selector(setAutomaticallyDetectsLanguage:)]) {
            request.automaticallyDetectsLanguage = YES;
        }
        request.usesLanguageCorrection = correction;
        request.minimumTextHeight = 0.012;

        VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
        NSError *performError = nil;
        BOOL success = [handler performRequests:@[request] error:&performError];
        CGImageRelease(image);

        NSMutableArray<NSString *> *texts = [NSMutableArray array];
        for (VNRecognizedTextObservation *observation in request.results ?: @[]) {
            VNRecognizedText *candidate = [observation topCandidates:1].firstObject;
            if (candidate.string.length > 0) [texts addObject:candidate.string];
        }
        writeJSON(@{
            @"image_loaded": @YES,
            @"recognition_level": level,
            @"language": language,
            @"uses_language_correction": @(correction),
            @"revision": @"VNRecognizeTextRequestRevision3",
            @"perform_success": @(success),
            @"perform_error_domain": performError.domain ?: [NSNull null],
            @"perform_error_code": performError == nil ? [NSNull null] : @(performError.code),
            @"perform_error": performError.localizedDescription ?: [NSNull null],
            @"completion_error_domain": completionError.domain ?: [NSNull null],
            @"completion_error_code": completionError == nil ? [NSNull null] : @(completionError.code),
            @"completion_error": completionError.localizedDescription ?: [NSNull null],
            @"results_nil": @(request.results == nil),
            @"result_count": @(request.results.count),
            @"raw_text": [texts componentsJoinedByString:@"\n"],
            @"os_version": NSProcessInfo.processInfo.operatingSystemVersionString,
            @"process_id": @(NSProcessInfo.processInfo.processIdentifier),
        });
        return success ? 0 : 4;
    }
}
