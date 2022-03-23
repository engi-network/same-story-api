#!/bin/bash
echo check run with args: "$@"

check_id=$1

echo running check $check_id

aws s3 cp s3://same-story/checks/$check_id /tmp/same-story/checks/$check_id --recursive

check=/tmp/same-story/checks/$check_id
check_repository=$(eval jq -r .repository $check/specification.json)
check_component=$(eval jq -r .component $check/specification.json)
check_story=$(eval jq -r .story $check/specification.json)

check_code=$check/code
[ -d $check_code ] && rm -rf $check_code
mkdir $check_code

echo cloning repository

gh repo clone $check_repository $check_code

cd $check_code

npm install

echo capturing code screenshots

#npm run storycap -- --serverTimeout 300000 --captureTimeout 300000
npm run storycap -- --viewport 400x300

echo uploading code screenshots to s3

aws s3 cp $check_code/__screenshots__ s3://same-story/checks/$check_id/report/__screenshots__ --recursive

echo running visual comparisons

check_frame=$check/frames/$check_component-$check_story.png 
check_code_screenshot=$check_code/__screenshots__/Example/$check_component/$check_story.png 

echo running regression with blue hightlight and uploading

blue_difference=blue_difference
compare $check_code_screenshot $check_frame -highlight-color blue $blue_difference.png
aws s3 cp $blue_difference.png s3://same-story/checks/$check_id/report/$blue_difference.png

echo running regression with gray hightlight and uploading

gray_difference=gray_difference
convert '(' $check_code_screenshot -flatten -grayscale Rec709Luminance ')' \
        '(' $check_frame -flatten -grayscale Rec709Luminance ')' \
        '(' -clone 0-1 -compose darken -composite ')' \
        -channel RGB -combine $gray_difference.png
aws s3 cp $gray_difference.png s3://same-story/checks/$check_id/report/$gray_difference.png

compare -metric MAE $check_code_screenshot $check_frame null: 2>&1 | xargs -0I{} echo "{ 'MAE': '{}' }" >> results.json
aws s3 cp results.json s3://same-story/checks/$check_id/report/results.json
