#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <ImageIO/ImageIO.h>
#import <CoreGraphics/CoreGraphics.h>

static void writeJSON(NSDictionary *payload) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:payload options:0 error:&error];
    if (data == nil) {
        fprintf(stderr, "無法序列化 OCR helper 結果：%s\n", error.localizedDescription.UTF8String);
        return;
    }
    fwrite(data.bytes, 1, data.length, stdout);
    fputc('\n', stdout);
    fflush(stdout);
}

static CGImageRef loadImage(NSString *path) {
    NSURL *url = [NSURL fileURLWithPath:path];
    CGImageSourceRef source = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
    if (source == NULL) return NULL;
    CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, NULL);
    CFRelease(source);
    return image;
}

static NSDictionary *errorResult(NSString *jobID, NSString *message) {
    return @{
        @"job_id": jobID ?: @"",
        @"raw_text": @"",
        @"confidence": @0.0,
        @"lines": @[],
        @"error": message ?: @"unknown Apple Vision error",
    };
}

static NSDictionary *recognizeJob(NSDictionary *job) {
    NSString *jobID = [job[@"job_id"] isKindOfClass:[NSString class]] ? job[@"job_id"] : @"";
    NSString *path = [job[@"image_path"] isKindOfClass:[NSString class]] ? job[@"image_path"] : nil;
    if (path == nil) return errorResult(jobID, @"image_path missing");
    CGImageRef image = loadImage(path);
    if (image == NULL) return errorResult(jobID, @"cannot load image");

    VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
    request.revision = VNRecognizeTextRequestRevision3;
    request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
    request.recognitionLanguages = @[@"zh-Hant"];
    request.usesLanguageCorrection = YES;
    request.minimumTextHeight = 0.012;
    VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
    NSError *requestError = nil;
    BOOL success = [handler performRequests:@[request] error:&requestError];
    CGImageRelease(image);
    if (!success) {
        NSString *message = requestError.localizedDescription ?: @"Vision request failed without NSError";
        return errorResult(jobID, message);
    }

    NSArray<VNRecognizedTextObservation *> *observations = [request.results sortedArrayUsingComparator:^NSComparisonResult(VNRecognizedTextObservation *left, VNRecognizedTextObservation *right) {
        CGFloat leftY = CGRectGetMidY(left.boundingBox);
        CGFloat rightY = CGRectGetMidY(right.boundingBox);
        if (fabs(leftY - rightY) > 0.03) return leftY > rightY ? NSOrderedAscending : NSOrderedDescending;
        CGFloat leftX = CGRectGetMinX(left.boundingBox);
        CGFloat rightX = CGRectGetMinX(right.boundingBox);
        if (leftX == rightX) return NSOrderedSame;
        return leftX < rightX ? NSOrderedAscending : NSOrderedDescending;
    }];
    NSMutableArray *lines = [NSMutableArray array];
    NSMutableArray<NSString *> *strings = [NSMutableArray array];
    double weightedConfidence = 0.0;
    NSUInteger totalCharacters = 0;
    for (VNRecognizedTextObservation *observation in observations) {
        VNRecognizedText *candidate = [observation topCandidates:1].firstObject;
        if (candidate == nil || candidate.string.length == 0) continue;
        CGRect box = observation.boundingBox;
        [strings addObject:candidate.string];
        [lines addObject:@{
            @"text": candidate.string,
            @"confidence": @(candidate.confidence),
            @"bbox": @[@(box.origin.x), @(box.origin.y), @(box.size.width), @(box.size.height)],
        }];
        NSUInteger count = MAX((NSUInteger)1, candidate.string.length);
        weightedConfidence += candidate.confidence * count;
        totalCharacters += count;
    }
    double confidence = totalCharacters == 0 ? 0.0 : weightedConfidence / totalCharacters;
    return @{
        @"job_id": jobID,
        @"raw_text": [strings componentsJoinedByString:@"\n"],
        @"confidence": @(confidence),
        @"lines": lines,
        @"error": [NSNull null],
    };
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "--probe") == 0) {
            VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
            request.revision = VNRecognizeTextRequestRevision3;
            request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
            NSError *error = nil;
            NSArray<NSString *> *languages = [request supportedRecognitionLanguagesAndReturnError:&error];
            writeJSON(@{
                @"available": @(languages != nil && [languages containsObject:@"zh-Hant"]),
                @"supported_languages": languages ?: @[],
                @"language": @"zh-Hant",
                @"revision": @"VNRecognizeTextRequestRevision3",
                @"recognition_level": @"accurate",
                @"os_version": NSProcessInfo.processInfo.operatingSystemVersionString,
                @"error": error == nil ? [NSNull null] : error.localizedDescription,
            });
            return languages == nil ? 3 : 0;
        }

        NSData *inputData = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
        NSString *input = [[NSString alloc] initWithData:inputData encoding:NSUTF8StringEncoding];
        if (input == nil) {
            fprintf(stderr, "OCR helper stdin 不是 UTF-8\n");
            return 2;
        }
        for (NSString *line in [input componentsSeparatedByCharactersInSet:NSCharacterSet.newlineCharacterSet]) {
            if (line.length == 0) continue;
            NSData *lineData = [line dataUsingEncoding:NSUTF8StringEncoding];
            NSError *error = nil;
            id value = [NSJSONSerialization JSONObjectWithData:lineData options:0 error:&error];
            if (![value isKindOfClass:[NSDictionary class]]) {
                writeJSON(errorResult(@"", error.localizedDescription ?: @"invalid JSON input"));
                continue;
            }
            @autoreleasepool {
                writeJSON(recognizeJob((NSDictionary *)value));
            }
        }
    }
    return 0;
}
